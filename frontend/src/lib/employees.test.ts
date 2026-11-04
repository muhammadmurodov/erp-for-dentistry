import { describe, expect, it } from 'vitest'
import {
  attendanceBlockedReason,
  codeCarriesHours,
  daysInMonth,
  employmentIsOver,
  isWeekend,
  isoDate,
  isoWeekday,
  nextEmploymentStatuses,
} from './employees'
import type { EmploymentStatus } from './types'

describe('employment transitions', () => {
  it('starts somebody who has not started yet', () => {
    expect(nextEmploymentStatuses('PROSPECTIVE')).toContain('ACTIVE')
  })

  it('suspends and reinstates', () => {
    expect(nextEmploymentStatuses('ACTIVE')).toContain('SUSPENDED')
    expect(nextEmploymentStatuses('SUSPENDED')).toContain('ACTIVE')
  })

  it('offers nothing once an employment has ended', () => {
    expect(nextEmploymentStatuses('ENDED')).toHaveLength(0)
    expect(employmentIsOver('ENDED')).toBe(true)
  })

  it('never offers ENDED as a plain state change', () => {
    // Leaving needs a date and a reason, so it gets its own control.
    for (const status of ['PROSPECTIVE', 'ACTIVE', 'SUSPENDED'] as EmploymentStatus[]) {
      expect(nextEmploymentStatuses(status)).not.toContain('ENDED')
    }
  })
})

describe('the month', () => {
  it('counts an ordinary month', () => {
    expect(daysInMonth(2026, 11)).toBe(30)
    expect(daysInMonth(2026, 1)).toBe(31)
  })

  it('counts February in a common year and a leap year', () => {
    expect(daysInMonth(2026, 2)).toBe(28)
    expect(daysInMonth(2024, 2)).toBe(29)
  })

  it('refuses an impossible month rather than guessing', () => {
    expect(daysInMonth(2026, 13)).toBe(0)
    expect(daysInMonth(2026, 0)).toBe(0)
  })

  it('builds a date without going through a timezone', () => {
    expect(isoDate(2026, 2, 3)).toBe('2026-02-03')
    expect(isoDate(2026, 11, 30)).toBe('2026-11-30')
  })

  it('reports Monday as 1 and Sunday as 7', () => {
    // 2026-11-02 is a Monday; 2026-11-08 is a Sunday.
    expect(isoWeekday(2026, 11, 2)).toBe(1)
    expect(isoWeekday(2026, 11, 8)).toBe(7)
  })

  it('marks the weekend', () => {
    expect(isWeekend(2026, 11, 7)).toBe(true)
    expect(isWeekend(2026, 11, 8)).toBe(true)
    expect(isWeekend(2026, 11, 9)).toBe(false)
  })
})

describe('which days may be recorded', () => {
  const active = {
    hired_on: '2026-01-01',
    ended_on: null,
    status: 'ACTIVE' as EmploymentStatus,
  }

  it('allows an ordinary past day', () => {
    expect(attendanceBlockedReason('2026-11-02', '2026-11-03', active, 'WORKED')).toBeNull()
  })

  it('refuses a day before somebody started', () => {
    expect(
      attendanceBlockedReason('2025-12-31', '2026-11-03', active, 'WORKED'),
    ).toBe('beforeHired')
  })

  it('refuses a day after somebody left', () => {
    expect(
      attendanceBlockedReason(
        '2026-11-03',
        '2026-11-03',
        { ...active, ended_on: '2026-10-31' },
        'WORKED',
      ),
    ).toBe('afterLeft')
  })

  it('refuses anybody who has not started', () => {
    expect(
      attendanceBlockedReason(
        '2026-11-03',
        '2026-11-03',
        { ...active, status: 'PROSPECTIVE' },
        'WORKED',
      ),
    ).toBe('notStarted')
  })

  it('refuses marking a future day as worked', () => {
    expect(attendanceBlockedReason('2026-11-10', '2026-11-03', active, 'WORKED')).toBe(
      'future',
    )
  })

  it('allows booking future leave', () => {
    expect(
      attendanceBlockedReason('2026-11-10', '2026-11-03', active, 'LEAVE'),
    ).toBeNull()
  })

  it('allows a future public holiday', () => {
    expect(
      attendanceBlockedReason('2026-11-10', '2026-11-03', active, 'HOLIDAY'),
    ).toBeNull()
  })

  it('allows opening a future cell before a code is chosen', () => {
    expect(attendanceBlockedReason('2026-11-10', '2026-11-03', active, null)).toBeNull()
  })
})

describe('hours', () => {
  it('belong to a day that was worked and to no other', () => {
    expect(codeCarriesHours('WORKED')).toBe(true)
    for (const code of ['ABSENT', 'SICK', 'LEAVE', 'HOLIDAY'] as const) {
      expect(codeCarriesHours(code)).toBe(false)
    }
  })
})
