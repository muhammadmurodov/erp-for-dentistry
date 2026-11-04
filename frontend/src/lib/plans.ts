import type { PlanStatus } from './types'

/**
 * Which plan state may follow which.
 *
 * This mirrors `PLAN_TRANSITIONS` in the backend's treatment service, and the
 * duplication is deliberate and narrow: the server is the authority and
 * refuses an illegal move with a 409 naming what it would have allowed. This
 * copy exists only so the screen does not *offer* a button that is going to
 * be refused. If the two ever disagree, the server wins and the user sees its
 * message — the worst case is an offered action that fails, never an accepted
 * one that should not have been.
 */
export const PLAN_TRANSITIONS: Record<PlanStatus, readonly PlanStatus[]> = {
  DRAFT: ['PROPOSED', 'ABANDONED'],
  // Back to draft is allowed: a patient asks for a change before agreeing,
  // and re-drafting is honest where editing a proposal in place is not.
  PROPOSED: ['ACCEPTED', 'DRAFT', 'ABANDONED'],
  ACCEPTED: ['IN_PROGRESS', 'ABANDONED'],
  IN_PROGRESS: ['COMPLETED', 'ABANDONED'],
  COMPLETED: [],
  ABANDONED: [],
}

/** The moves offered from `status`. Empty for a plan that has finished. */
export function nextStatuses(status: PlanStatus): readonly PlanStatus[] {
  return PLAN_TRANSITIONS[status] ?? []
}

/** True once a plan can no longer move. */
export function isFinal(status: PlanStatus): boolean {
  return nextStatuses(status).length === 0
}

/**
 * May this plan be marked finished right now?
 *
 * Two conditions, and the second is the one worth saying out loud: a plan
 * cannot be completed while a tooth on it is still open. The server enforces
 * it; the screen explains it before the button is pressed rather than after.
 */
export function completionBlockedReason(
  status: PlanStatus,
  openItemCount: number,
): 'notInProgress' | 'workOutstanding' | null {
  if (status !== 'IN_PROGRESS') return 'notInProgress'
  if (openItemCount > 0) return 'workOutstanding'
  return null
}

/**
 * Progress through a plan, as a fraction, for a plain bar.
 *
 * Cancelled and corrected items are not counted at all — on either side.
 * Counting a cancelled tooth as "done" would show a plan as finished when the
 * work was abandoned, and counting it as outstanding would show a plan that
 * can never reach the end.
 */
export function planProgress(
  items: readonly { status: string }[],
): { done: number; total: number } {
  const counted = items.filter(
    (i) => i.status !== 'CANCELLED' && i.status !== 'CORRECTED',
  )
  return {
    done: counted.filter((i) => i.status === 'COMPLETED').length,
    total: counted.length,
  }
}
