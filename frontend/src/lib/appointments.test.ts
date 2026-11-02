import { describe, expect, it } from 'vitest'
import {
  actionsFor,
  clashWith,
  formatClinicTime,
  isClosed,
  minutesOfDay,
  toApiTime,
  type Slot,
} from './appointments'

function slot(overrides: Partial<Slot> = {}): Slot {
  return {
    dentist_user_id: 'dentist-1',
    scheduled_on: '2026-10-06',
    scheduled_at: '15:00:00',
    duration_minutes: 30,
    status: 'SCHEDULED',
    ...overrides,
  }
}

describe('what may still happen to a booking', () => {
  it('offers confirming only while it is merely booked', () => {
    expect(actionsFor('SCHEDULED').confirm).toBe(true)
    expect(actionsFor('CONFIRMED').confirm).toBe(false)
    expect(actionsFor('ARRIVED').confirm).toBe(false)
  })

  it('offers finishing only once the patient is here', () => {
    expect(actionsFor('ARRIVED').complete).toBe(true)
    expect(actionsFor('SCHEDULED').complete).toBe(false)
  })

  it('does not offer a no-show for somebody who has arrived', () => {
    expect(actionsFor('ARRIVED').noShow).toBe(false)
    expect(actionsFor('SCHEDULED').noShow).toBe(true)
  })

  it('offers nothing on a closed booking but leaves it readable', () => {
    for (const status of ['COMPLETED', 'CANCELLED', 'NO_SHOW'] as const) {
      const actions = actionsFor(status)
      expect(Object.values(actions).some(Boolean), status).toBe(false)
      expect(isClosed(status)).toBe(true)
    }
  })

  it('allows moving a booking that is still live', () => {
    expect(actionsFor('CONFIRMED').reschedule).toBe(true)
    expect(actionsFor('CANCELLED').reschedule).toBe(false)
  })
})

describe('times, without timezone arithmetic', () => {
  it('reads what the API sends', () => {
    expect(minutesOfDay('15:00:00')).toBe(900)
    expect(minutesOfDay('09:30:00')).toBe(570)
    expect(minutesOfDay('00:00:00')).toBe(0)
  })

  it('shows the hour the clinic wrote, not the reader s local hour', () => {
    // The whole point: 15:00 in Tashkent is 15:00 on the screen, whatever
    // the browser's timezone is set to.
    expect(formatClinicTime('15:00:00')).toBe('15:00')
    expect(formatClinicTime('9:05')).toBe('09:05')
  })

  it('has nothing to show for a booking with no hour', () => {
    expect(formatClinicTime(null)).toBe('')
    expect(minutesOfDay(null)).toBeNull()
  })

  it('refuses an impossible time', () => {
    expect(minutesOfDay('25:00')).toBeNull()
    expect(minutesOfDay('12:75')).toBeNull()
    expect(minutesOfDay('afternoon')).toBeNull()
  })

  it('sends back what the API takes', () => {
    expect(toApiTime('15:00')).toBe('15:00:00')
    expect(toApiTime(' 9:05 ')).toBe('09:05:00')
    expect(toApiTime('nonsense')).toBeNull()
  })
})

describe('one dentist, one chair', () => {
  it('finds the booking a new one would land on', () => {
    const existing = [slot({ id: 'a' })]
    const clash = clashWith(slot({ scheduled_at: '15:15:00' }), existing)
    expect(clash?.id).toBe('a')
  })

  it('allows back-to-back bookings', () => {
    const existing = [slot({ id: 'a', scheduled_at: '15:00:00', duration_minutes: 30 })]
    expect(clashWith(slot({ scheduled_at: '15:30:00' }), existing)).toBeNull()
  })

  it('allows another dentist the same hour', () => {
    const existing = [slot({ id: 'a' })]
    expect(clashWith(slot({ dentist_user_id: 'dentist-2' }), existing)).toBeNull()
  })

  it('allows the same hour on another day', () => {
    const existing = [slot({ id: 'a' })]
    expect(clashWith(slot({ scheduled_on: '2026-10-07' }), existing)).toBeNull()
  })

  it('ignores a cancelled booking, which has given its hour up', () => {
    const existing = [slot({ id: 'a', status: 'CANCELLED' })]
    expect(clashWith(slot(), existing)).toBeNull()
  })

  it('ignores a finished booking', () => {
    const existing = [slot({ id: 'a', status: 'COMPLETED' })]
    expect(clashWith(slot(), existing)).toBeNull()
  })

  it('does not treat a day-booking as filling the day', () => {
    const existing = [slot({ id: 'a', scheduled_at: null, duration_minutes: null })]
    expect(clashWith(slot(), existing)).toBeNull()
  })

  it('does not compare a proposed booking that has no hour', () => {
    const existing = [slot({ id: 'a' })]
    expect(clashWith(slot({ scheduled_at: null }), existing)).toBeNull()
  })

  it('does not clash with itself when a booking is being moved', () => {
    const existing = [slot({ id: 'a', scheduled_at: '15:00:00' })]
    const moving = slot({ id: 'a', scheduled_at: '15:00:00', duration_minutes: 60 })
    expect(clashWith(moving, existing)).toBeNull()
  })

  it('finds a clash with a long booking that starts earlier', () => {
    const existing = [slot({ id: 'a', scheduled_at: '09:00:00', duration_minutes: 120 })]
    expect(clashWith(slot({ scheduled_at: '10:30:00' }), existing)?.id).toBe('a')
  })

  it('does not compare a booking with no dentist', () => {
    const existing = [slot({ id: 'a', dentist_user_id: null })]
    expect(clashWith(slot({ dentist_user_id: null }), existing)).toBeNull()
  })
})
