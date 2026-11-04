import { describe, expect, it } from 'vitest'
import {
  amountByCode,
  balanceSheetWarning,
  blockedBy,
  blockedStatutoryReports,
  comparativeDate,
  comparativeWindow,
  drillDownQuery,
  hasUnconfirmedAccounts,
  lastDayOfMonth,
  mergedCodes,
  periodWindow,
  reportIsAvailable,
  sectionIsEmpty,
  variance,
  variancePercent,
} from './reports'
import type { ReportCatalogue, StatementSection } from './types'

function section(lines: [string, string][]): StatementSection {
  return {
    key: 'revenue',
    lines: lines.map(([account_code, amount]) => ({
      account_id: `id-${account_code}`,
      account_code,
      account_name: `Account ${account_code}`,
      element: 'REVENUE' as const,
      amount,
      debit: '0.00',
      credit: amount,
      needs_confirmation: false,
    })),
    total: '0.00',
  }
}

const catalogue: ReportCatalogue = {
  reports: [
    { key: 'profit_and_loss', available: true, blocked_by: null, statutory: false },
    { key: 'cash_flow', available: true, blocked_by: null, statutory: false },
    {
      key: 'statutory_financial_statements',
      available: false,
      blocked_by: 'A2',
      statutory: true,
    },
    { key: 'statutory_cash_flow', available: false, blocked_by: 'A3', statutory: true },
    { key: 'vat_return', available: false, blocked_by: 'A5', statutory: true },
  ],
  accounts_needing_confirmation: 4,
  chart_unconfirmed: true,
}

describe('period windows', () => {
  it('covers a whole month, including a 31-day one', () => {
    expect(periodWindow('this_month', '2026-01-15')).toEqual({
      from: '2026-01-01',
      to: '2026-01-31',
    })
  })

  it('knows February in a leap year and in an ordinary one', () => {
    expect(lastDayOfMonth(2024, 2)).toBe(29)
    expect(lastDayOfMonth(2026, 2)).toBe(28)
    expect(periodWindow('this_month', '2024-02-10').to).toBe('2024-02-29')
  })

  it('steps back over a year boundary for last month', () => {
    expect(periodWindow('last_month', '2026-01-15')).toEqual({
      from: '2025-12-01',
      to: '2025-12-31',
    })
  })

  it('puts each month in the right quarter', () => {
    expect(periodWindow('this_quarter', '2026-02-10')).toEqual({
      from: '2026-01-01',
      to: '2026-03-31',
    })
    expect(periodWindow('this_quarter', '2026-11-04')).toEqual({
      from: '2026-10-01',
      to: '2026-12-31',
    })
    expect(periodWindow('this_quarter', '2026-12-31')).toEqual({
      from: '2026-10-01',
      to: '2026-12-31',
    })
  })

  it('ends the year to date today, not at the year end', () => {
    expect(periodWindow('year_to_date', '2026-11-04')).toEqual({
      from: '2026-01-01',
      to: '2026-11-04',
    })
  })

  it('gives last year in full', () => {
    expect(periodWindow('last_year', '2026-11-04')).toEqual({
      from: '2025-01-01',
      to: '2025-12-31',
    })
  })
})

describe('comparatives', () => {
  it('compares with the same months a year earlier', () => {
    expect(comparativeWindow({ from: '2026-09-01', to: '2026-09-30' })).toEqual({
      from: '2025-09-01',
      to: '2025-09-30',
    })
  })

  it('steps 29 February back to the 28th rather than out of the month', () => {
    expect(comparativeDate('2024-02-29')).toBe('2023-02-28')
  })

  it('compares a balance sheet with the same day a year earlier', () => {
    expect(comparativeDate('2026-12-31')).toBe('2025-12-31')
  })
})

describe('variance', () => {
  it('subtracts exactly, at a magnitude a float would get wrong', () => {
    expect(variance('9007199254740993.00', '1.00')).toBe('9007199254740992.00')
  })

  it('reports a fall as a negative', () => {
    expect(variance('300000.00', '500000.00')).toBe('-200000.00')
  })

  it('gives the percentage to one decimal place', () => {
    expect(variancePercent('150000.00', '100000.00')).toBe(50)
    expect(variancePercent('50000.00', '100000.00')).toBe(-50)
    expect(variancePercent('133333.00', '100000.00')).toBe(33.3)
  })

  it('refuses to express growth from nothing as a percentage', () => {
    // Inventing "infinite growth" is inventing a figure.
    expect(variancePercent('100000.00', '0.00')).toBeNull()
  })

  it('handles a prior period that was negative', () => {
    expect(variancePercent('0.00', '-100000.00')).toBe(100)
  })
})

describe('reading two periods together', () => {
  it('finds an account that only exists in one of them', () => {
    const current = section([['9030', '300000.00']])
    const prior = section([
      ['9030', '200000.00'],
      ['9040', '50000.00'],
    ])
    expect(mergedCodes(current, prior)).toEqual(['9030', '9040'])
    expect(amountByCode(prior, '9040')).toBe('50000.00')
  })

  it('treats a missing account as zero rather than undefined', () => {
    expect(amountByCode(section([]), '9030')).toBe('0.00')
    expect(amountByCode(undefined, '9030')).toBe('0.00')
  })

  it('knows when there is nothing to show at all', () => {
    expect(sectionIsEmpty(section([]), section([]))).toBe(true)
    expect(sectionIsEmpty(section([]), undefined)).toBe(true)
    expect(sectionIsEmpty(section([['9030', '1.00']]), undefined)).toBe(false)
    expect(sectionIsEmpty(section([]), section([['9030', '1.00']]))).toBe(false)
  })
})

describe('the capability catalogue', () => {
  it('takes the backend at its word about what exists', () => {
    expect(reportIsAvailable(catalogue, 'profit_and_loss')).toBe(true)
    expect(reportIsAvailable(catalogue, 'statutory_cash_flow')).toBe(false)
  })

  it('treats an unknown report as unavailable rather than guessing', () => {
    expect(reportIsAvailable(catalogue, 'something_new')).toBe(false)
    expect(reportIsAvailable(undefined, 'profit_and_loss')).toBe(false)
  })

  it('names the question blocking a report', () => {
    expect(blockedBy(catalogue, 'statutory_financial_statements')).toBe('A2')
    expect(blockedBy(catalogue, 'statutory_cash_flow')).toBe('A3')
    expect(blockedBy(catalogue, 'vat_return')).toBe('A5')
  })

  it('says nothing blocks a report that works', () => {
    expect(blockedBy(catalogue, 'profit_and_loss')).toBeNull()
  })

  it('lists the statutory forms that are still blocked', () => {
    expect(blockedStatutoryReports(catalogue).map((r) => r.key)).toEqual([
      'statutory_financial_statements',
      'statutory_cash_flow',
      'vat_return',
    ])
  })
})

describe('warnings the screen must not forget', () => {
  it('flags a balance sheet that does not balance', () => {
    const sheet = { balances: false } as never
    expect(balanceSheetWarning(sheet)).toBe('unbalanced')
  })

  it('says nothing about one that does', () => {
    const sheet = { balances: true } as never
    expect(balanceSheetWarning(sheet)).toBe('none')
    expect(balanceSheetWarning(undefined)).toBe('none')
  })

  it('notices when a statement rests on unconfirmed classifications', () => {
    expect(hasUnconfirmedAccounts({ accounts_needing_confirmation: 3 } as never)).toBe(true)
    expect(hasUnconfirmedAccounts({ accounts_needing_confirmation: 0 } as never)).toBe(false)
    expect(hasUnconfirmedAccounts(undefined)).toBe(false)
  })
})

describe('drill-down', () => {
  it('carries the report window into the account statement', () => {
    expect(drillDownQuery({ from: '2026-01-01', to: '2026-03-31' })).toBe(
      'from_date=2026-01-01&to_date=2026-03-31',
    )
  })
})
