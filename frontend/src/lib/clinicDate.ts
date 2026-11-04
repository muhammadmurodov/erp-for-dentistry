/**
 * The clinic's own date, not the browser's and not the server's.
 *
 * This exists because `new Date().toISOString()` gives the **UTC** date, and
 * between seven in the evening and midnight in Tashkent that is yesterday —
 * exactly when the clinic is still seeing patients. Every screen that asks
 * "what is today" has to ask it in Asia/Tashkent or it will book an
 * appointment, or file a day's attendance, against the wrong day for five
 * hours out of every twenty-four.
 *
 * It was written twice before it was a module: once in the appointment book
 * and once in the timesheet. A third copy would have been the one that
 * drifted.
 */

/** The IANA zone the clinic works in. One definition, used everywhere. */
export const CLINIC_TIME_ZONE = 'Asia/Tashkent'

/** Today in the clinic's zone, as `YYYY-MM-DD`. */
export function todayInClinic(now: Date = new Date()): string {
  return new Intl.DateTimeFormat('en-CA', {
    timeZone: CLINIC_TIME_ZONE,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).format(now)
}

/**
 * A day either side, by arithmetic on the date itself.
 *
 * Built in UTC and read back in UTC, so no timezone can shift the result by
 * one on the way through.
 */
export function shiftDay(day: string, by: number): string {
  const [year = 1970, month = 1, date = 1] = day.split('-').map(Number)
  return new Date(Date.UTC(year, month - 1, date + by)).toISOString().slice(0, 10)
}

/** A month either side, keeping the day of the month where it can. */
export function shiftMonth(
  year: number,
  month: number,
  by: number,
): { year: number; month: number } {
  const zeroBased = year * 12 + (month - 1) + by
  return { year: Math.floor(zeroBased / 12), month: (zeroBased % 12) + 1 }
}
