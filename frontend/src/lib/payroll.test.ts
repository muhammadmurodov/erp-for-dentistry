import { describe, expect, it } from 'vitest'
import {
  byKindNewestFirst,
  canRecalculate,
  describeRate,
  employerCost,
  governs,
  isBlocked,
  isSettled,
  missingKinds,
  nextAction,
  periodCode,
  runTotals,
  salaryOn,
  splitLines,
} from './payroll'
import type { PayrollRule, Payslip, PayslipLine } from './types'

function rule(overrides: Partial<PayrollRule> = {}): PayrollRule {
  return {
    id: 'rule-1',
    kind: 'INCOME_TAX',
    method: 'PERCENT_OF_GROSS',
    rate: '12.0000',
    fixed_amount: null,
    effective_from: '2019-01-01',
    effective_to: null,
    component_of: null,
    legal_reference: 'Flat 12% from 1 January 2019',
    source_url: null,
    applicability: null,
    notes: null,
    status: 'APPROVED',
    approved_by_user_id: 'user-1',
    ...overrides,
  }
}

function line(overrides: Partial<PayslipLine> = {}): PayslipLine {
  return {
    id: 'line-1',
    category: 'EARNING',
    code: 'SALARY',
    description: 'Salary',
    amount: '1000000.00',
    rate: null,
    statutory_rule_id: null,
    note: null,
    ...overrides,
  }
}

/** The brief's own worked example, as the backend returns it. */
function slip(overrides: Partial<Payslip> = {}): Payslip {
  return {
    id: 'slip-1',
    employee_id: 'employee-1',
    worked_days: '11',
    working_days: 22,
    monthly_salary: '2000000.00',
    gross: '1000000.00',
    employee_deductions: '120000.00',
    employer_contributions: '120000.00',
    net: '880000.00',
    lines: [],
    ...overrides,
  }
}

describe('which act comes next', () => {
  it('walks the four acts in order', () => {
    expect(nextAction('DRAFT')).toBe('calculate')
    expect(nextAction('CALCULATED')).toBe('approve')
    expect(nextAction('APPROVED')).toBe('post')
    expect(nextAction('POSTED')).toBe('pay')
  })

  it('offers nothing once a run is paid or cancelled', () => {
    expect(nextAction('PAID')).toBeNull()
    expect(nextAction('CANCELLED')).toBeNull()
  })

  it('never offers posting before approval', () => {
    // The backend refuses it; the screen must not invite it.
    expect(nextAction('CALCULATED')).not.toBe('post')
    expect(nextAction('DRAFT')).not.toBe('post')
  })

  it('lets an open run be calculated again and a settled one never', () => {
    expect(canRecalculate('DRAFT')).toBe(true)
    expect(canRecalculate('CALCULATED')).toBe(true)
    expect(canRecalculate('APPROVED')).toBe(false)
    expect(canRecalculate('POSTED')).toBe(false)
  })

  it('treats approved, posted and paid as history', () => {
    expect(isSettled('APPROVED')).toBe(true)
    expect(isSettled('PAID')).toBe(true)
    expect(isSettled('CALCULATED')).toBe(false)
  })
})

describe('a blocked run', () => {
  it('is blocked when the backend named a reason', () => {
    expect(isBlocked({ blocked_reasons: ['The social tax rate is not approved.'] })).toBe(true)
  })

  it('is not blocked when nothing stands in the way', () => {
    expect(isBlocked({ blocked_reasons: [] })).toBe(false)
  })
})

describe('the period', () => {
  it('pads the month so it sorts', () => {
    expect(periodCode({ year: 2026, month: 3 })).toBe('2026-03')
    expect(periodCode({ year: 2026, month: 11 })).toBe('2026-11')
  })
})

describe('the three sides of a payslip', () => {
  const lines = [
    line(),
    line({ id: 'l2', category: 'EMPLOYEE_DEDUCTION', code: 'INCOME_TAX', amount: '119000.00' }),
    line({ id: 'l3', category: 'EMPLOYEE_DEDUCTION', code: 'INPS', amount: '1000.00' }),
    line({ id: 'l4', category: 'EMPLOYER_CONTRIBUTION', code: 'SOCIAL_TAX', amount: '120000.00' }),
  ]

  it('keeps them apart rather than netting them', () => {
    const split = splitLines(lines)
    expect(split.earnings).toHaveLength(1)
    expect(split.deductions.map((row) => row.code)).toEqual(['INCOME_TAX', 'INPS'])
    expect(split.contributions.map((row) => row.code)).toEqual(['SOCIAL_TAX'])
  })

  it('never files an employer contribution as a deduction', () => {
    const split = splitLines(lines)
    expect(split.deductions.some((row) => row.code === 'SOCIAL_TAX')).toBe(false)
  })

  it('preserves the order the calculation produced', () => {
    expect(splitLines(lines).deductions[0]?.code).toBe('INCOME_TAX')
  })
})

describe('what employing somebody cost', () => {
  it('adds the employer contribution to gross', () => {
    // 1,000,000 gross + 120,000 social tax. Worked out by hand.
    expect(employerCost(slip())).toBe('1120000.00')
  })

  it('is not net pay', () => {
    expect(employerCost(slip())).not.toBe(slip().net)
  })

  it('equals gross when there is no employer contribution', () => {
    expect(employerCost(slip({ employer_contributions: '0.00' }))).toBe('1000000.00')
  })
})

describe('the month totals', () => {
  it('sums three payslips without float drift', () => {
    const totals = runTotals([slip(), slip({ id: 's2' }), slip({ id: 's3' })])
    expect(totals.gross).toBe('3000000.00')
    expect(totals.deductions).toBe('360000.00')
    expect(totals.contributions).toBe('360000.00')
    expect(totals.net).toBe('2640000.00')
    expect(totals.cost).toBe('3360000.00')
  })

  it('keeps the odd som that a float would lose', () => {
    const totals = runTotals([
      slip({
        gross: '0.10',
        net: '0.10',
        employee_deductions: '0.00',
        employer_contributions: '0.00',
      }),
      slip({
        id: 's2',
        gross: '0.20',
        net: '0.20',
        employee_deductions: '0.00',
        employer_contributions: '0.00',
      }),
    ])
    expect(totals.gross).toBe('0.30')
  })

  it('is zero for a run with no payslips rather than an error', () => {
    expect(runTotals([]).gross).toBe('0.00')
  })

  it('reconciles: net plus deductions equals gross', () => {
    const totals = runTotals([slip(), slip({ id: 's2' })])
    expect(totals.net).toBe('1760000.00')
    expect(totals.deductions).toBe('240000.00')
    expect(totals.gross).toBe('2000000.00')
  })
})

describe('reading a rate', () => {
  it('drops the trailing zeros a column carries', () => {
    expect(describeRate(rule())).toBe('12%')
    expect(describeRate(rule({ rate: '0.1000' }))).toBe('0.1%')
  })

  it('shows the fixed amount where there is no rate', () => {
    expect(
      describeRate(rule({ method: 'FIXED_AMOUNT', rate: null, fixed_amount: '50000.00' })),
    ).toBe('50000.00')
  })

  it('says nothing rather than guessing when a percent rule has no rate', () => {
    expect(describeRate(rule({ rate: null }))).toBeNull()
  })
})

describe('which rule governs a date', () => {
  it('ignores a draft, however well dated', () => {
    expect(governs(rule({ status: 'DRAFT' }), '2026-03-31')).toBe(false)
  })

  it('ignores a rule that has not started', () => {
    expect(governs(rule({ effective_from: '2027-01-01' }), '2026-03-31')).toBe(false)
  })

  it('ignores a rule that has ended', () => {
    expect(governs(rule({ effective_to: '2018-12-31' }), '2026-03-31')).toBe(false)
  })

  it('accepts a rule on the day it starts and the day it ends', () => {
    expect(governs(rule({ effective_from: '2026-03-31' }), '2026-03-31')).toBe(true)
    expect(governs(rule({ effective_to: '2026-03-31' }), '2026-03-31')).toBe(true)
  })
})

describe('what is missing before a run can be approved', () => {
  it('names both when nothing is approved', () => {
    expect(missingKinds([], '2026-03-31')).toEqual(['INCOME_TAX', 'SOCIAL_TAX'])
  })

  it('leaves only the social tax once the income tax is approved', () => {
    expect(missingKinds([rule()], '2026-03-31')).toEqual(['SOCIAL_TAX'])
  })

  it('is satisfied once both govern the date', () => {
    const social = rule({ id: 'r2', kind: 'SOCIAL_TAX', rate: '12.0000' })
    expect(missingKinds([rule(), social], '2026-03-31')).toEqual([])
  })

  it('does not count a draft social tax as an approved one', () => {
    const social = rule({ id: 'r2', kind: 'SOCIAL_TAX', status: 'DRAFT' })
    expect(missingKinds([rule(), social], '2026-03-31')).toEqual(['SOCIAL_TAX'])
  })

  it('does not require INPS, which is part of the income tax', () => {
    expect(missingKinds([], '2026-03-31')).not.toContain('INPS')
  })
})

describe('the rule history', () => {
  it('groups by kind and puts the newest version of each first', () => {
    const rows = byKindNewestFirst([
      rule({ id: 'a', kind: 'INCOME_TAX', effective_from: '2019-01-01' }),
      rule({ id: 'b', kind: 'INCOME_TAX', effective_from: '2024-01-01' }),
      rule({ id: 'c', kind: 'EMPLOYEE_PENSION', effective_from: '2018-01-01' }),
    ])
    expect(rows.map((row) => row.id)).toEqual(['c', 'b', 'a'])
  })

  it('leaves the array it was given alone', () => {
    const input = [
      rule({ id: 'a', effective_from: '2019-01-01' }),
      rule({ id: 'b', effective_from: '2024-01-01' }),
    ]
    byKindNewestFirst(input)
    expect(input.map((row) => row.id)).toEqual(['a', 'b'])
  })
})

describe('the salary in force on a date', () => {
  const agreements = [
    { amount: '2000000.00', effective_from: '2025-01-01', effective_to: '2026-02-28' },
    { amount: '2500000.00', effective_from: '2026-03-01', effective_to: null },
  ]

  it('is the one covering the date, not the latest row', () => {
    expect(salaryOn(agreements, '2026-01-15')).toBe('2000000.00')
    expect(salaryOn(agreements, '2026-03-15')).toBe('2500000.00')
  })

  it('is nothing at all before the first agreement', () => {
    expect(salaryOn(agreements, '2024-12-31')).toBeNull()
  })

  it('is nothing when there is no agreement, rather than zero', () => {
    // Zero would read as "works for free"; nothing reads as "not agreed yet",
    // and the backend pays neither.
    expect(salaryOn([], '2026-03-15')).toBeNull()
  })
})
