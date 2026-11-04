import { describe, expect, it } from 'vitest'
import {
  completionBlockedReason,
  isFinal,
  nextStatuses,
  planProgress,
  PLAN_TRANSITIONS,
} from './plans'
import type { PlanStatus } from './types'

describe('plan transitions', () => {
  it('walks the ordinary course from draft to finished', () => {
    let status: PlanStatus = 'DRAFT'
    const walked: PlanStatus[] = [status]
    for (const next of ['PROPOSED', 'ACCEPTED', 'IN_PROGRESS', 'COMPLETED'] as const) {
      expect(nextStatuses(status)).toContain(next)
      status = next
      walked.push(status)
    }
    expect(walked).toHaveLength(5)
  })

  it('offers no move from a finished plan', () => {
    expect(nextStatuses('COMPLETED')).toHaveLength(0)
    expect(isFinal('COMPLETED')).toBe(true)
  })

  it('offers no move from an abandoned plan', () => {
    expect(nextStatuses('ABANDONED')).toHaveLength(0)
    expect(isFinal('ABANDONED')).toBe(true)
  })

  it('lets a proposal go back to draft', () => {
    expect(nextStatuses('PROPOSED')).toContain('DRAFT')
  })

  it('never offers a jump from draft straight to finished', () => {
    expect(nextStatuses('DRAFT')).not.toContain('COMPLETED')
  })

  it('offers abandonment from every live state', () => {
    for (const status of ['DRAFT', 'PROPOSED', 'ACCEPTED', 'IN_PROGRESS'] as const) {
      expect(nextStatuses(status)).toContain('ABANDONED')
    }
  })

  it('never offers a move back to the state it is already in', () => {
    for (const status of Object.keys(PLAN_TRANSITIONS) as PlanStatus[]) {
      expect(nextStatuses(status)).not.toContain(status)
    }
  })
})

describe('completion', () => {
  it('is blocked before work has started', () => {
    expect(completionBlockedReason('ACCEPTED', 0)).toBe('notInProgress')
  })

  it('is blocked while a tooth is still open', () => {
    expect(completionBlockedReason('IN_PROGRESS', 1)).toBe('workOutstanding')
  })

  it('is allowed once the work is done', () => {
    expect(completionBlockedReason('IN_PROGRESS', 0)).toBeNull()
  })
})

describe('progress', () => {
  it('counts completed work against the whole plan', () => {
    expect(
      planProgress([
        { status: 'COMPLETED' },
        { status: 'COMPLETED' },
        { status: 'IN_PROGRESS' },
      ]),
    ).toEqual({ done: 2, total: 3 })
  })

  it('ignores cancelled work on both sides', () => {
    expect(
      planProgress([{ status: 'COMPLETED' }, { status: 'CANCELLED' }]),
    ).toEqual({ done: 1, total: 1 })
  })

  it('ignores a corrected item, which its replacement already counts', () => {
    expect(
      planProgress([{ status: 'CORRECTED' }, { status: 'COMPLETED' }]),
    ).toEqual({ done: 1, total: 1 })
  })

  it('reports an empty plan as nothing out of nothing', () => {
    expect(planProgress([])).toEqual({ done: 0, total: 0 })
  })
})
