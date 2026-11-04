import type { AttendanceCode, EmploymentStatus } from './types'

/**
 * Which employment state may follow which.
 *
 * Mirrors `STATUS_TRANSITIONS` in the backend's employee service, for the
 * same narrow reason the plan table is mirrored: so the screen does not offer
 * a button the server is going to refuse. The server remains the authority.
 *
 * `ENDED` is reachable from every live state but is not offered here —
 * ending an employment needs a date and a reason, so it has its own control
 * and its own endpoint rather than being one entry in a list of states.
 */
export const EMPLOYMENT_TRANSITIONS: Record<
  EmploymentStatus,
  readonly EmploymentStatus[]
> = {
  PROSPECTIVE: ['ACTIVE'],
  ACTIVE: ['SUSPENDED'],
  SUSPENDED: ['ACTIVE'],
  ENDED: [],
}

export function nextEmploymentStatuses(
  status: EmploymentStatus,
): readonly EmploymentStatus[] {
  return EMPLOYMENT_TRANSITIONS[status] ?? []
}

/** True once an employment can no longer change. */
export function employmentIsOver(status: EmploymentStatus): boolean {
  return status === 'ENDED'
}

/**
 * The days of one month, as a timesheet needs them.
 *
 * Built from a year and a month rather than from `Date` arithmetic on
 * "today", because the clinic's month is not the container's: a timesheet
 * opened at 02:00 in Tashkent on the first must not show the previous month.
 * The caller passes the clinic's year and month; this only counts days.
 */
export function daysInMonth(year: number, month: number): number {
  if (month < 1 || month > 12) return 0
  return new Date(Date.UTC(year, month, 0)).getUTCDate()
}

/** `2026-02-03`, built without going through a timezone. */
export function isoDate(year: number, month: number, day: number): string {
  const mm = String(month).padStart(2, '0')
  const dd = String(day).padStart(2, '0')
  return `${year}-${mm}-${dd}`
}

/**
 * ISO weekday (1 Monday … 7 Sunday) for a day of a month.
 *
 * `Date.getUTCDay()` returns 0 for Sunday, which is the off-by-one that puts
 * a rota out by a day. Converted here, once.
 */
export function isoWeekday(year: number, month: number, day: number): number {
  const weekday = new Date(Date.UTC(year, month - 1, day)).getUTCDay()
  return weekday === 0 ? 7 : weekday
}

/** Saturday and Sunday, for shading a timesheet. Not a rule, just a hint. */
export function isWeekend(year: number, month: number, day: number): boolean {
  return isoWeekday(year, month, day) >= 6
}

/**
 * May this day be recorded against this employment?
 *
 * Mirrors the server's refusals so a cell that cannot be filled is not
 * offered: before somebody started, after they left, or a future day for
 * anything but agreed leave. `null` means it may.
 */
export function attendanceBlockedReason(
  date: string,
  today: string,
  employee: { hired_on: string; ended_on: string | null; status: EmploymentStatus },
  code: AttendanceCode | null,
): 'beforeHired' | 'afterLeft' | 'notStarted' | 'future' | null {
  if (employee.status === 'PROSPECTIVE') return 'notStarted'
  if (date < employee.hired_on) return 'beforeHired'
  if (employee.ended_on !== null && date > employee.ended_on) return 'afterLeft'
  if (date > today && code !== null && code !== 'LEAVE' && code !== 'HOLIDAY') {
    return 'future'
  }
  return null
}

/** Codes that carry hours. Exactly one, and the server agrees. */
export function codeCarriesHours(code: AttendanceCode): boolean {
  return code === 'WORKED'
}
