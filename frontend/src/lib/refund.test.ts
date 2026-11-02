import { describe, expect, it } from 'vitest'
import { isWholePayment, refundBody, refundPlan, type RefundabilityView } from './refund'

function view(overrides: Partial<RefundabilityView> = {}): RefundabilityView {
  return {
    amount: '500000.00',
    allocated: '300000.00',
    refunded: '0.00',
    refundable: '200000.00',
    currency: 'UZS',
    may_refund: true,
    needs_cash_session: true,
    ...overrides,
  }
}

const base = { typed: '', openSessionId: 'session-1', mayRefund: true }

describe('what the dialog may offer', () => {
  it('offers everything that is left when nothing is typed', () => {
    const plan = refundPlan({ ...base, view: view() })
    expect(plan.amount).toBe(20000000n)
    expect(plan.remaining).toBe(0n)
    expect(plan.submittable).toBe(true)
  })

  it('offers part of it when part is typed', () => {
    const plan = refundPlan({ ...base, view: view(), typed: '50000' })
    expect(plan.amount).toBe(5000000n)
    expect(plan.remaining).toBe(15000000n)
    expect(plan.mode).toBe('partial')
  })

  it('refuses more than is refundable, and says so rather than silently capping', () => {
    const plan = refundPlan({ ...base, view: view(), typed: '250000' })
    expect(plan.error).toBe('overRefundable')
    expect(plan.submittable).toBe(false)
  })

  it('refuses nothing at all', () => {
    expect(refundPlan({ ...base, view: view(), typed: '0' }).error).toBe('mustBePositive')
  })

  it('refuses something that is not an amount', () => {
    expect(refundPlan({ ...base, view: view(), typed: 'abc' }).error).toBe('notANumber')
    expect(refundPlan({ ...base, view: view(), typed: '-100' }).error).toBe('notANumber')
  })

  it('accepts a comma as the decimal separator, which a Russian keyboard gives', () => {
    expect(refundPlan({ ...base, view: view(), typed: '1000,50' }).amount).toBe(100050n)
  })
})

describe('which refund it is', () => {
  it('reverses the payment when the whole of it is untouched', () => {
    const plan = refundPlan({
      ...base,
      view: view({ allocated: '0.00', refundable: '500000.00' }),
    })
    expect(plan.mode).toBe('full')
    expect(refundBody(plan, ' taken in error ')).toEqual({
      reason: 'taken in error',
      amount: null,
    })
  })

  it('hands back part when part of the payment settled a bill', () => {
    // Reversing it would unsettle a bill the patient really did pay.
    const plan = refundPlan({ ...base, view: view() })
    expect(plan.mode).toBe('partial')
    expect(refundBody(plan, 'overpaid')).toEqual({
      reason: 'overpaid',
      amount: '200000.00',
    })
  })

  it('hands back part when something was already given back', () => {
    const plan = refundPlan({
      ...base,
      view: view({ allocated: '0.00', refunded: '100000.00', refundable: '400000.00' }),
    })
    expect(plan.mode).toBe('partial')
    expect(isWholePayment(view({ allocated: '0.00', refunded: '100000.00' }), 50000000n)).toBe(
      false,
    )
  })

  it('treats a part equal to the whole amount as partial once a bill is involved', () => {
    expect(isWholePayment(view(), 50000000n)).toBe(false)
  })
})

describe('the dead ends, which are not the same dead end', () => {
  it('says the money is on bills when none of it is refundable', () => {
    const plan = refundPlan({
      ...base,
      view: view({
        amount: '300000.00',
        allocated: '300000.00',
        refundable: '0.00',
        may_refund: false,
      }),
    })
    expect(plan.blocked).toBe('allOnBills')
    expect(plan.submittable).toBe(false)
  })

  it('says it has already been given back when refunds used it up', () => {
    const plan = refundPlan({
      ...base,
      view: view({
        allocated: '0.00',
        refunded: '500000.00',
        refundable: '0.00',
        may_refund: false,
      }),
    })
    expect(plan.blocked).toBe('alreadyRefunded')
  })

  it('will not refund cash with no drawer open', () => {
    const plan = refundPlan({ ...base, view: view(), openSessionId: null })
    expect(plan.blocked).toBe('needsOpenDrawer')
    expect(plan.submittable).toBe(false)
  })

  it('refunds a card payment with no drawer open, because no cash moves', () => {
    const plan = refundPlan({
      ...base,
      view: view({ needs_cash_session: false }),
      openSessionId: null,
    })
    expect(plan.blocked).toBeNull()
    expect(plan.submittable).toBe(true)
  })

  it('offers nothing without the permission', () => {
    const plan = refundPlan({ ...base, view: view(), mayRefund: false })
    expect(plan.blocked).toBe('noPermission')
  })
})

describe('exactness', () => {
  it('subtracts without floating point error', () => {
    // 1 000 000.10 - 0.10 in doubles is not reliably 1 000 000.
    const plan = refundPlan({
      ...base,
      view: view({ allocated: '0.00', amount: '1000000.10', refundable: '1000000.10' }),
      typed: '0.10',
    })
    expect(plan.remaining).toBe(100000000n)
  })

  it('keeps two decimals in what it sends', () => {
    const plan = refundPlan({
      ...base,
      view: view({ allocated: '0.00', refundable: '500000.00' }),
      typed: '1234.5',
    })
    expect(refundBody(plan, 'part').amount).toBe('1234.50')
  })
})
