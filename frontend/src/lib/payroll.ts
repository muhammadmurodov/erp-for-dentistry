import { ZERO, formatAmount, parseAmount } from './money'
import type {
  PayrollRule,
  PayrollRun,
  PayrollRunStatus,
  Payslip,
  PayslipLine,
  RuleStatus,
  StatutoryRuleKind,
} from './types'

/**
 * The rules the payroll screens apply, kept out of the components.
 *
 * Two things here are deliberate and worth stating, because a payroll screen
 * that gets either wrong misleads somebody about their own pay.
 *
 * **An employer contribution is not a deduction.** Social tax is a cost of
 * employing somebody and never reduces what they take home. `splitLines`
 * separates the three categories rather than netting them, and
 * `employerCost` adds the contribution to gross instead of subtracting it
 * from anything. The backend enforces the same separation in its own types;
 * this mirrors it so that no arithmetic in a component can quietly undo it.
 *
 * **A rate is never computed here.** Every rate arrives from an approved
 * rule and is only ever displayed. There is no function in this file that
 * multiplies a wage by a percentage, and there should never be one: the
 * calculation belongs to the backend, where the approved rule versions live
 * and where each payslip line records which version produced it.
 */

/** A run nobody has approved yet, which may still be recalculated. */
function isOpen(status: PayrollRunStatus): boolean {
  return status === 'DRAFT' || status === 'CALCULATED'
}

/** Once a run is here, its payslips are history and are never recomputed. */
export function isSettled(status: PayrollRunStatus): boolean {
  return status === 'APPROVED' || status === 'POSTED' || status === 'PAID'
}

/** The four acts, in order. Posting and paying are separate on purpose. */
export type RunAction = 'calculate' | 'approve' | 'post' | 'pay'

/**
 * The one act that is available next, or null when the run is finished or
 * cancelled.
 *
 * It returns a single action rather than a set because a run is a sequence:
 * offering "post" beside "calculate" would invite somebody to skip approval,
 * which the backend refuses anyway. The screen should not offer what the
 * server will reject.
 */
export function nextAction(status: PayrollRunStatus): RunAction | null {
  switch (status) {
    case 'DRAFT':
      return 'calculate'
    case 'CALCULATED':
      return 'approve'
    case 'APPROVED':
      return 'post'
    case 'POSTED':
      return 'pay'
    default:
      return null
  }
}

/** A calculated run may be calculated again; an approved one may not. */
export function canRecalculate(status: PayrollRunStatus): boolean {
  return isOpen(status)
}

/** The permission the next act needs, so the screen can hide what it cannot do. */
export const ACTION_PERMISSION: Record<RunAction, string> = {
  calculate: 'payroll.run.calculate',
  approve: 'payroll.run.approve',
  post: 'payroll.run.post',
  pay: 'payroll.run.post',
}

/**
 * Whether anything stands in the way of approving or posting this run.
 *
 * A blocked run is not an error and not a failure: it is the control working.
 * The reasons are sentences the backend wrote, naming the rule and the open
 * question behind it, and the screen shows them verbatim rather than
 * summarising them into "blocked".
 */
export function isBlocked(run: Pick<PayrollRun, 'blocked_reasons'>): boolean {
  return run.blocked_reasons.length > 0
}

/** `2026-03` for a run, which sorts and reads the same in every locale. */
export function periodCode(run: Pick<PayrollRun, 'year' | 'month'>): string {
  return `${run.year}-${String(run.month).padStart(2, '0')}`
}

export interface SplitLines {
  earnings: PayslipLine[]
  deductions: PayslipLine[]
  contributions: PayslipLine[]
}

/**
 * The payslip's three sides, kept apart.
 *
 * Order within each side is preserved from the API, which emits the salary
 * before the adjustments and the statutory lines in the order they were
 * applied — so a payslip reads the way it was computed.
 */
export function splitLines(lines: readonly PayslipLine[]): SplitLines {
  return {
    earnings: lines.filter((line) => line.category === 'EARNING'),
    deductions: lines.filter((line) => line.category === 'EMPLOYEE_DEDUCTION'),
    contributions: lines.filter((line) => line.category === 'EMPLOYER_CONTRIBUTION'),
  }
}

/**
 * What employing somebody cost for the period: gross plus the employer's own
 * contributions, which is also what the accounting entry debits to expense.
 *
 * Deliberately *not* `gross - deductions`: that is net pay, a different
 * figure answering a different question, and confusing the two is how a
 * clinic underestimates its own wage bill by the social tax.
 */
export function employerCost(slip: Pick<Payslip, 'gross' | 'employer_contributions'>): string {
  return formatAmount(parseAmount(slip.gross) + parseAmount(slip.employer_contributions))
}

export interface RunTotals {
  gross: string
  deductions: string
  contributions: string
  net: string
  cost: string
}

/** The month's totals, summed in minor units so no float rounding creeps in. */
export function runTotals(slips: readonly Payslip[]): RunTotals {
  let gross = ZERO
  let deductions = ZERO
  let contributions = ZERO
  let net = ZERO
  for (const slip of slips) {
    gross += parseAmount(slip.gross)
    deductions += parseAmount(slip.employee_deductions)
    contributions += parseAmount(slip.employer_contributions)
    net += parseAmount(slip.net)
  }
  return {
    gross: formatAmount(gross),
    deductions: formatAmount(deductions),
    contributions: formatAmount(contributions),
    net: formatAmount(net),
    cost: formatAmount(gross + contributions),
  }
}

/**
 * A rate as it should be read, or the fixed amount where there is no rate.
 *
 * Trailing zeros are dropped because "12%" is what the law says and "12.00%"
 * is what a database column says.
 */
export function describeRate(
  rule: Pick<PayrollRule, 'method' | 'rate' | 'fixed_amount'>,
): string | null {
  if (rule.method === 'PERCENT_OF_GROSS') {
    if (rule.rate === null) return null
    const trimmed = rule.rate.replace(/(\.\d*?)0+$/, '$1').replace(/\.$/, '')
    return `${trimmed}%`
  }
  return rule.fixed_amount
}

/** A rule governs a date when it has started and has not yet ended. */
export function governs(
  rule: Pick<PayrollRule, 'effective_from' | 'effective_to' | 'status'>,
  on: string,
): boolean {
  if (rule.status !== 'APPROVED') return false
  if (rule.effective_from > on) return false
  return rule.effective_to === null || rule.effective_to >= on
}

/**
 * The kinds a run needs before it can be approved: the employee's income tax
 * and the employer's social tax.
 *
 * INPS is not among them because it is a component of the income tax rather
 * than a charge of its own, and the employee pension was abolished from
 * 2019 — a period before that needs it, which is the backend's judgment to
 * make from the dates, not this list's.
 */
const REQUIRED_KINDS: readonly StatutoryRuleKind[] = ['INCOME_TAX', 'SOCIAL_TAX'] as const

/**
 * Which required kinds have no approved rule governing the date.
 *
 * This is a *display* convenience for the rules tab, letting it show what is
 * missing before anybody opens a run. It is not the authority: the backend
 * re-checks at approval and again at posting, and its answer is the one that
 * decides. A screen that disagreed with it would simply be refused.
 */
export function missingKinds(rules: readonly PayrollRule[], on: string): StatutoryRuleKind[] {
  return REQUIRED_KINDS.filter(
    (kind) => !rules.some((rule) => rule.kind === kind && governs(rule, on)),
  )
}

/** Rules grouped by kind, each newest first, which is how a history reads. */
export function byKindNewestFirst(rules: readonly PayrollRule[]): PayrollRule[] {
  return [...rules].sort((a, b) => {
    if (a.kind !== b.kind) return a.kind.localeCompare(b.kind)
    return b.effective_from.localeCompare(a.effective_from)
  })
}

/** Draft rules calculate nothing, so the screen says so where they appear. */
export function isDraft(status: RuleStatus): boolean {
  return status === 'DRAFT'
}

/**
 * The salary in force on a date, from an employee's agreements.
 *
 * A rise is a new row rather than an edit, so "the salary" is always a
 * question about a date and never about the latest row — the latest row may
 * start next month.
 */
export function salaryOn(
  agreements: readonly { amount: string; effective_from: string; effective_to: string | null }[],
  on: string,
): string | null {
  const covering = agreements.filter(
    (row) => row.effective_from <= on && (row.effective_to === null || row.effective_to >= on),
  )
  if (covering.length === 0) return null
  const latest = covering.reduce((best, row) =>
    row.effective_from > best.effective_from ? row : best,
  )
  return latest.amount
}
