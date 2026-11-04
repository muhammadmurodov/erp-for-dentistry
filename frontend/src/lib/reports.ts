import { formatAmount, parseAmount } from './money'
import type { BalanceSheet, ProfitAndLoss, ReportCatalogue, StatementSection } from './types'

/**
 * The rules the reports screens apply, kept out of the components.
 *
 * Two things live here and nothing else does. The first is period
 * arithmetic — which days a named period covers, and which earlier period it
 * is naturally compared with — because getting "last month" wrong by a day
 * is the sort of defect nobody notices until an accountant does. The second
 * is reading the backend's capability catalogue, so the screen never decides
 * for itself whether a report exists.
 *
 * Amounts are compared and subtracted in exact minor units, never as
 * JavaScript numbers: a som figure large enough to matter does not survive a
 * double.
 */

export type PeriodKey =
  | 'this_month'
  | 'last_month'
  | 'this_quarter'
  | 'year_to_date'
  | 'last_year'

export interface DateWindow {
  from: string
  to: string
}

function iso(year: number, month: number, day: number): string {
  const pad = (value: number) => String(value).padStart(2, '0')
  return `${year}-${pad(month)}-${pad(day)}`
}

/** The last day of a month, the only part of this that is not obvious. */
export function lastDayOfMonth(year: number, month: number): number {
  return new Date(Date.UTC(year, month, 0)).getUTCDate()
}

/**
 * The days a named period covers, relative to a given day.
 *
 * `today` is passed in rather than read from the clock, so this is testable
 * and so the clinic's own day — which is not the browser's timezone — stays
 * the caller's decision (ADR-046).
 */
export function periodWindow(key: PeriodKey, today: string): DateWindow {
  const [year, month] = today.split('-').map(Number) as [number, number, number]

  switch (key) {
    case 'this_month':
      return { from: iso(year, month, 1), to: iso(year, month, lastDayOfMonth(year, month)) }
    case 'last_month': {
      const m = month === 1 ? 12 : month - 1
      const y = month === 1 ? year - 1 : year
      return { from: iso(y, m, 1), to: iso(y, m, lastDayOfMonth(y, m)) }
    }
    case 'this_quarter': {
      const first = Math.floor((month - 1) / 3) * 3 + 1
      const last = first + 2
      return { from: iso(year, first, 1), to: iso(year, last, lastDayOfMonth(year, last)) }
    }
    case 'year_to_date':
      return { from: iso(year, 1, 1), to: today }
    case 'last_year':
      return { from: iso(year - 1, 1, 1), to: iso(year - 1, 12, 31) }
  }
}

/**
 * The period a window is naturally compared with: the same months a year
 * earlier.
 *
 * A year back rather than the immediately preceding window, because a dental
 * clinic's months are not comparable — August is quiet and September is not —
 * and comparing like with like is the only comparison worth printing.
 */
export function comparativeWindow(window: DateWindow): DateWindow {
  return { from: shiftYear(window.from, -1), to: shiftYear(window.to, -1) }
}

function shiftYear(value: string, by: number): string {
  const [year, month, day] = value.split('-').map(Number) as [number, number, number]
  const shifted = year + by
  // 29 February only exists in a leap year; stepping back to the 28th is the
  // conventional answer and the only one that keeps a window inside its year.
  const maxDay = lastDayOfMonth(shifted, month)
  return iso(shifted, month, Math.min(day, maxDay))
}

/** The same day a year earlier, for a balance sheet's comparative column. */
export function comparativeDate(asOf: string): string {
  return shiftYear(asOf, -1)
}

/** Exact difference between two amounts, as a decimal string. */
export function variance(current: string, prior: string): string {
  return formatAmount(parseAmount(current) - parseAmount(prior))
}

/**
 * The change as a percentage, to one decimal place, or null when there is
 * nothing to compare against.
 *
 * Null rather than zero or infinity: growth from nothing is not a
 * percentage, and printing one would be inventing a figure.
 */
export function variancePercent(current: string, prior: string): number | null {
  const before = parseAmount(prior)
  if (before === 0n) return null
  const change = parseAmount(current) - before
  const scaled = (change * 1000n) / (before < 0n ? -before : before)
  return Number(scaled) / 10
}

/** Look up one line's amount in a section, for a comparative column. */
export function amountByCode(section: StatementSection | undefined, code: string): string {
  if (!section) return '0.00'
  return section.lines.find((line) => line.account_code === code)?.amount ?? '0.00'
}

/** Every account code appearing in either period, in statement order. */
export function mergedCodes(
  current: StatementSection,
  prior: StatementSection | undefined,
): string[] {
  const codes = new Set(current.lines.map((line) => line.account_code))
  for (const line of prior?.lines ?? []) codes.add(line.account_code)
  return [...codes].sort()
}

/** True when a section has nothing in it and nothing to compare. */
export function sectionIsEmpty(
  current: StatementSection,
  prior: StatementSection | undefined,
): boolean {
  return current.lines.length === 0 && (prior?.lines.length ?? 0) === 0
}

/**
 * Does this build offer the report?
 *
 * Answered from the backend's catalogue, never from a list in the frontend:
 * a screen that decided for itself is how a working report ends up behind a
 * "not developed yet" message.
 */
export function reportIsAvailable(catalogue: ReportCatalogue | undefined, key: string): boolean {
  return catalogue?.reports.find((report) => report.key === key)?.available ?? false
}

/** Which open question blocks a report, if one does. */
export function blockedBy(catalogue: ReportCatalogue | undefined, key: string): string | null {
  const report = catalogue?.reports.find((entry) => entry.key === key)
  if (!report || report.available) return null
  return report.blocked_by
}

/** The statutory forms, which stay blocked until an accountant confirms them. */
export function blockedStatutoryReports(catalogue: ReportCatalogue | undefined) {
  return (catalogue?.reports ?? []).filter((report) => report.statutory && !report.available)
}

/**
 * Whether a balance sheet should be shown with a warning.
 *
 * The server already says whether it balanced; this exists so the screen
 * cannot forget to ask.
 */
export function balanceSheetWarning(sheet: BalanceSheet | undefined): 'none' | 'unbalanced' {
  if (!sheet) return 'none'
  return sheet.balances ? 'none' : 'unbalanced'
}

/** True when any line on the statement rests on an unconfirmed classification. */
export function hasUnconfirmedAccounts(
  report: ProfitAndLoss | BalanceSheet | undefined,
): boolean {
  return (report?.accounts_needing_confirmation ?? 0) > 0
}

/** The query string for drilling from a report line into its entries. */
export function drillDownQuery(window: DateWindow): string {
  return `from_date=${window.from}&to_date=${window.to}`
}
