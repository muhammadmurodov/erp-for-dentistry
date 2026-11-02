/**
 * The day's bookings, as a screen has to show them.
 *
 * Three things here rather than in the component, because each is a rule
 * worth being sure about:
 *
 * * **What may still happen to a booking.** A cancelled appointment cannot
 *   be confirmed, a finished one cannot become a no-show, and offering
 *   either is offering a button that will fail.
 * * **Whether two bookings collide.** The server refuses an overlap and is
 *   the authority; this warns while the receptionist is still typing, which
 *   is the only moment the warning is any use.
 * * **How a time reads.** Times come from the API as `HH:MM:SS` in the
 *   clinic's own zone. They are not instants and must not be put through a
 *   timezone conversion on the way to the screen — `new Date('15:00:00')` is
 *   not a date at all, and `new Date('2026-10-06T15:00:00')` is read as
 *   local time, which is only right by accident.
 */

/** Live bookings claim their slot; closed ones have given it up. */
export type AppointmentStatus =
  | 'SCHEDULED'
  | 'CONFIRMED'
  | 'ARRIVED'
  | 'COMPLETED'
  | 'CANCELLED'
  | 'NO_SHOW'

export const CLOSED_STATUSES: readonly AppointmentStatus[] = [
  'COMPLETED',
  'CANCELLED',
  'NO_SHOW',
]

export function isClosed(status: AppointmentStatus): boolean {
  return CLOSED_STATUSES.includes(status)
}

/** What a receptionist may still do to this booking. */
export function actionsFor(status: AppointmentStatus): {
  confirm: boolean
  arrive: boolean
  complete: boolean
  cancel: boolean
  noShow: boolean
  reschedule: boolean
} {
  const open = !isClosed(status)
  return {
    confirm: status === 'SCHEDULED',
    // The patient is here. Opening a visit does this too, from the
    // clinical side — which is why it is offered and not required.
    arrive: status === 'SCHEDULED' || status === 'CONFIRMED',
    complete: status === 'ARRIVED',
    cancel: open,
    // Somebody who has already arrived did not fail to turn up.
    noShow: status === 'SCHEDULED' || status === 'CONFIRMED',
    reschedule: open,
  }
}

/** Minutes since midnight, for comparing two bookings on one day. */
export function minutesOfDay(at: string | null): number | null {
  if (at === null || at === '') return null
  const match = /^(\d{1,2}):(\d{2})/.exec(at)
  if (!match) return null
  const hours = Number(match[1])
  const minutes = Number(match[2])
  if (hours > 23 || minutes > 59) return null
  return hours * 60 + minutes
}

/** `HH:MM`, as the clinic writes it. No timezone arithmetic. */
export function formatClinicTime(at: string | null): string {
  const minutes = minutesOfDay(at)
  if (minutes === null) return ''
  const hours = Math.floor(minutes / 60)
  return `${String(hours).padStart(2, '0')}:${String(minutes % 60).padStart(2, '0')}`
}

/** `HH:MM` back to what the API takes. */
export function toApiTime(typed: string): string | null {
  const minutes = minutesOfDay(typed.trim())
  if (minutes === null) return null
  const hours = Math.floor(minutes / 60)
  return `${String(hours).padStart(2, '0')}:${String(minutes % 60).padStart(2, '0')}:00`
}

export interface Slot {
  id?: string
  dentist_user_id: string | null
  scheduled_on: string
  scheduled_at: string | null
  duration_minutes: number | null
  status: AppointmentStatus
}

/**
 * Which live booking the proposed one would collide with, if any.
 *
 * Only where both have a time *and* a duration: the clinic books "Tuesday"
 * as often as "Tuesday at three", and treating a day-booking as filling the
 * whole day would stop the day being booked at all. Touching is not
 * overlapping — 15:00–15:30 and 15:30–16:00 are how a day is filled.
 */
export function clashWith(proposed: Slot, existing: readonly Slot[]): Slot | null {
  const start = minutesOfDay(proposed.scheduled_at)
  if (start === null || proposed.duration_minutes === null) return null
  if (proposed.dentist_user_id === null) return null
  const end = start + proposed.duration_minutes

  for (const other of existing) {
    if (other.id !== undefined && other.id === proposed.id) continue
    if (isClosed(other.status)) continue
    if (other.dentist_user_id !== proposed.dentist_user_id) continue
    if (other.scheduled_on !== proposed.scheduled_on) continue
    const otherStart = minutesOfDay(other.scheduled_at)
    if (otherStart === null || other.duration_minutes === null) continue
    const otherEnd = otherStart + other.duration_minutes
    if (start < otherEnd && otherStart < end) return other
  }
  return null
}
