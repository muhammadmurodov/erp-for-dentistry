import { describe, expect, it } from 'vitest'
import { shiftDay, shiftMonth, todayInClinic } from './clinicDate'

describe('the clinic’s today', () => {
  it('is the Tashkent date, not the UTC one', () => {
    // 21:30 UTC on the 2nd is already 02:30 on the 3rd in Tashkent — the
    // five hours in which a UTC date books against the wrong day.
    const evening = new Date('2026-11-02T21:30:00Z')
    expect(evening.toISOString().slice(0, 10)).toBe('2026-11-02')
    expect(todayInClinic(evening)).toBe('2026-11-03')
  })

  it('agrees with UTC during the clinic’s working hours', () => {
    const midday = new Date('2026-11-03T06:00:00Z')
    expect(todayInClinic(midday)).toBe('2026-11-03')
  })
})

describe('shifting a day', () => {
  it('moves forward and back', () => {
    expect(shiftDay('2026-11-03', 1)).toBe('2026-11-04')
    expect(shiftDay('2026-11-03', -1)).toBe('2026-11-02')
  })

  it('crosses a month boundary', () => {
    expect(shiftDay('2026-11-30', 1)).toBe('2026-12-01')
    expect(shiftDay('2026-12-01', -1)).toBe('2026-11-30')
  })

  it('crosses a year boundary', () => {
    expect(shiftDay('2026-12-31', 1)).toBe('2027-01-01')
  })

  it('handles the end of February in a leap year', () => {
    expect(shiftDay('2024-02-28', 1)).toBe('2024-02-29')
    expect(shiftDay('2026-02-28', 1)).toBe('2026-03-01')
  })
})

describe('shifting a month', () => {
  it('moves forward and back', () => {
    expect(shiftMonth(2026, 11, 1)).toEqual({ year: 2026, month: 12 })
    expect(shiftMonth(2026, 11, -1)).toEqual({ year: 2026, month: 10 })
  })

  it('crosses a year boundary in both directions', () => {
    expect(shiftMonth(2026, 12, 1)).toEqual({ year: 2027, month: 1 })
    expect(shiftMonth(2026, 1, -1)).toEqual({ year: 2025, month: 12 })
  })

  it('moves a whole year at a time', () => {
    expect(shiftMonth(2026, 5, 12)).toEqual({ year: 2027, month: 5 })
  })
})
