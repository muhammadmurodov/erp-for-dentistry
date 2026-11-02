/**
 * What may be given back, and by which route.
 *
 * Two refunds exist in this system and they are not the same operation:
 *
 * * **Reversing the payment.** The money should never have been taken. Every
 *   entry it made is reversed, any bill it settled reopens, and the payment
 *   becomes `REFUNDED`. Only possible while nothing has been given back yet.
 * * **Handing back part of it.** The patient overpaid and wants the
 *   difference. The payment stays live, what it settled stays settled, and
 *   each refund is a record of its own so a second may follow the first.
 *
 * The screen has to choose between them and say why, and the arithmetic has
 * to be exact — so it is done here, on `bigint` minor units, and tested. The
 * backend enforces the same rules under a row lock and is the authority;
 * this decides what the dialog offers and what it refuses to submit.
 */

import { clampToZero, formatAmount, parseAmount, parseInputAmount, ZERO } from './money'

export interface RefundabilityView {
  amount: string
  allocated: string
  refunded: string
  refundable: string
  currency: string
  may_refund: boolean
  needs_cash_session: boolean
}

export type RefundBlocked =
  | 'alreadyRefunded'
  /** Every som of it is settling a bill. Needs a credit note, not a refund. */
  | 'allOnBills'
  /** Cash, and no drawer is open to take it out of. */
  | 'needsOpenDrawer'
  | 'noPermission'

export type RefundError = 'notANumber' | 'mustBePositive' | 'overRefundable'

export interface RefundPlan {
  /** `full` reverses the payment; `partial` hands back part of the advance. */
  mode: 'full' | 'partial'
  /** Minor units that would be refunded. */
  amount: bigint
  /** What remains refundable afterwards, for the confirmation line. */
  remaining: bigint
  blocked: RefundBlocked | null
  error: RefundError | null
  /** True only when the dialog may submit. */
  submittable: boolean
}

export interface RefundContext {
  view: RefundabilityView
  /** What the cashier typed. Empty means "all of what is left". */
  typed: string
  /** An open cash session's id, where one is open. */
  openSessionId: string | null
  mayRefund: boolean
}

/**
 * Whether a refund of `amount` is the whole payment reversed.
 *
 * Only when nothing of it is on a bill and nothing has been given back
 * already: otherwise reversing it would unsettle a bill the patient did pay,
 * or pay the refunded part out a second time.
 */
export function isWholePayment(view: RefundabilityView, amount: bigint): boolean {
  return (
    parseAmount(view.allocated) === ZERO &&
    parseAmount(view.refunded) === ZERO &&
    amount === parseAmount(view.amount)
  )
}

export function refundPlan({
  view,
  typed,
  openSessionId,
  mayRefund,
}: RefundContext): RefundPlan {
  const refundable = clampToZero(parseAmount(view.refundable))
  const blank: Omit<RefundPlan, 'blocked' | 'error'> = {
    mode: 'partial',
    amount: ZERO,
    remaining: refundable,
    submittable: false,
  }

  if (!mayRefund) return { ...blank, blocked: 'noPermission', error: null }
  if (!view.may_refund) {
    // Two different dead ends, and the difference is the whole point: one is
    // "there is nothing left", the other is "this money is not refundable at
    // all, the bill has to be credited".
    const spent = parseAmount(view.allocated) > ZERO && refundable === ZERO
    const given = parseAmount(view.refunded) > ZERO && refundable === ZERO
    return {
      ...blank,
      blocked: given ? 'alreadyRefunded' : spent ? 'allOnBills' : 'alreadyRefunded',
      error: null,
    }
  }
  if (view.needs_cash_session && openSessionId === null) {
    return { ...blank, blocked: 'needsOpenDrawer', error: null }
  }

  // An empty box means all of what is left, which is what somebody who
  // presses the button without typing means.
  const amount = typed.trim() === '' ? refundable : parseInputAmount(typed)
  if (amount === null) return { ...blank, blocked: null, error: 'notANumber' }
  if (amount <= ZERO) return { ...blank, blocked: null, error: 'mustBePositive' }
  if (amount > refundable) {
    return { ...blank, amount, blocked: null, error: 'overRefundable' }
  }

  return {
    mode: isWholePayment(view, amount) ? 'full' : 'partial',
    amount,
    remaining: refundable - amount,
    blocked: null,
    error: null,
    submittable: true,
  }
}

/** The body a refund request carries. `null` amount means "reverse it all". */
export function refundBody(plan: RefundPlan, reason: string): {
  reason: string
  amount: string | null
} {
  return {
    reason: reason.trim(),
    amount: plan.mode === 'full' ? null : formatAmount(plan.amount),
  }
}
