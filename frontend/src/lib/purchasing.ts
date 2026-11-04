import type { BillStatus, OrderStatus, SupplierInvoice } from './types'

/**
 * Which order state may follow which, for the moves a person makes.
 *
 * Mirrors `ORDER_TRANSITIONS` in the backend, narrowly and for one reason:
 * so the screen does not offer a button the server is going to refuse. The
 * server remains the authority and names what it would have allowed.
 *
 * Receiving moves an order on its own — DRAFT and PARTIALLY_RECEIVED arrive
 * through a delivery, not through a button — so those are not here as
 * choices anybody makes.
 */
export const ORDER_TRANSITIONS: Record<OrderStatus, readonly OrderStatus[]> = {
  DRAFT: ['APPROVED', 'CANCELLED'],
  APPROVED: ['SENT', 'CANCELLED'],
  SENT: ['CANCELLED', 'CLOSED'],
  PARTIALLY_RECEIVED: ['CLOSED'],
  RECEIVED: [],
  CANCELLED: [],
  CLOSED: [],
}

export function nextOrderStatuses(status: OrderStatus): readonly OrderStatus[] {
  return ORDER_TRANSITIONS[status] ?? []
}

/** True once an order can no longer move by anybody's decision. */
export function orderIsFinished(status: OrderStatus): boolean {
  return nextOrderStatuses(status).length === 0
}

/** An order still expecting a delivery. */
export function orderAwaitsDelivery(status: OrderStatus): boolean {
  return status === 'APPROVED' || status === 'SENT' || status === 'PARTIALLY_RECEIVED'
}

/** Closing an order short is the only move that demands a reason. */
export function orderMoveNeedsReason(status: OrderStatus): boolean {
  return status === 'CLOSED'
}

/**
 * May this bill be paid right now?
 *
 * Only a registered one, and only while something is left on it. A draft is
 * not in the books and owes nothing; a voided one has had its posting
 * reversed.
 */
export function billIsPayable(bill: Pick<SupplierInvoice, 'status' | 'remaining'>): boolean {
  return bill.status === 'REGISTERED' && Number(bill.remaining) > 0
}

/** A registered bill is immutable; correcting one means voiding it. */
export function billIsEditable(status: BillStatus): boolean {
  return status === 'DRAFT'
}

/**
 * The total of a set of lines, as a string, without going through a float.
 *
 * Money never becomes a JavaScript number here: `0.1 + 0.2` is not `0.3`,
 * and a som amount large enough to matter loses precision as a double. The
 * arithmetic is done in integer minor units.
 */
export function lineTotal(quantity: string, unitPrice: string): string {
  const q = toMinorUnits(quantity, 4)
  const p = toMinorUnits(unitPrice, 4)
  if (q === null || p === null) return '0.00'
  // q and p each carry 4 implied decimals, so the product carries 8.
  const product = q * p
  return fromMinorUnits(product, 8)
}

export function sumAmounts(amounts: readonly string[]): string {
  let total = 0n
  for (const amount of amounts) {
    const value = toMinorUnits(amount, 2)
    if (value === null) continue
    total += value
  }
  return fromMinorUnits(total, 2)
}

/** `"12.34"` → `1234n` at scale 2. `null` when it is not a number at all. */
export function toMinorUnits(value: string, scale: number): bigint | null {
  const trimmed = value.trim()
  if (!/^-?\d*(\.\d*)?$/.test(trimmed) || trimmed === '' || trimmed === '.') {
    return null
  }
  const negative = trimmed.startsWith('-')
  const unsigned = negative ? trimmed.slice(1) : trimmed
  const [whole = '0', fraction = ''] = unsigned.split('.')
  const padded = (fraction + '0'.repeat(scale)).slice(0, scale)
  const digits = `${whole === '' ? '0' : whole}${padded}`
  const result = BigInt(digits)
  return negative ? -result : result
}

/** `1234n` at scale 2 → `"12.34"`, always with at least two decimals. */
export function fromMinorUnits(value: bigint, scale: number): string {
  const negative = value < 0n
  const digits = (negative ? -value : value).toString().padStart(scale + 1, '0')
  const whole = digits.slice(0, digits.length - scale) || '0'
  let fraction = scale > 0 ? digits.slice(digits.length - scale) : ''
  // Trim to two places, which is what a som is counted in, but never below.
  fraction = fraction.replace(/0+$/, '')
  fraction = (fraction + '00').slice(0, Math.max(2, fraction.length))
  return `${negative ? '-' : ''}${whole}.${fraction}`
}
