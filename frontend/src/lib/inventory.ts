import type {
  AdjustmentStatus,
  ExpiringRow,
  StockBalanceRow,
  StockMovementKind,
} from './types'

/**
 * The rules the stock screens apply, kept out of the components.
 *
 * Everything here is a pure function over strings, because quantities are
 * decimals carried as strings for the same reason money is: a JavaScript
 * number cannot hold 0.1 + 0.2, and a stock figure that reads
 * 2.9999999999999996 is how somebody ends up writing off a difference that
 * was never there.
 *
 * **Quantities are not money and do not share its arithmetic.** `money.ts`
 * works in two decimal places because that is the scale of a currency. A
 * material is bought by the box and used by the millilitre, and the column
 * holding it is `NUMERIC(20, 4)` — so these carry four, and borrowing the
 * money helpers would silently truncate 0.0005 of an anaesthetic to nothing.
 */

const SCALE = 4
const FACTOR = 10n ** BigInt(SCALE)
const ZERO = 0n

/** Parses a quantity the API sent: a decimal string, possibly negative. */
export function parseQuantity(value: string | null | undefined): bigint {
  if (value === null || value === undefined || value === '') return ZERO
  const trimmed = value.trim()
  const negative = trimmed.startsWith('-')
  const unsigned = negative ? trimmed.slice(1) : trimmed
  if (!/^\d+(\.\d+)?$/.test(unsigned)) return ZERO
  const [whole = '0', fraction = ''] = unsigned.split('.')
  const minor = BigInt(whole) * FACTOR + BigInt(fraction.slice(0, SCALE).padEnd(SCALE, '0'))
  return negative ? -minor : minor
}

/**
 * Parses a quantity a person typed. Returns null for anything that is not
 * one, including a negative: taking stock off is its own operation with its
 * own permission, not a minus sign in a box.
 */
export function parseQuantityInput(input: string): bigint | null {
  const trimmed = input.trim().replace(/[\s ]/g, '')
  if (trimmed === '') return ZERO
  if (!/^\d+([.,]\d{0,4})?$/.test(trimmed)) return null
  const [whole = '0', fraction = ''] = trimmed.replace(',', '.').split('.')
  return BigInt(whole) * FACTOR + BigInt(fraction.padEnd(SCALE, '0'))
}

/**
 * Renders a quantity for a person: trailing zeros dropped, because "10 box"
 * is what the clinic says and "10.0000 box" is what a database says.
 */
export function formatQuantity(minor: bigint): string {
  const negative = minor < ZERO
  const unsigned = negative ? -minor : minor
  const whole = unsigned / FACTOR
  const fraction = (unsigned % FACTOR).toString().padStart(SCALE, '0').replace(/0+$/, '')
  return `${negative ? '-' : ''}${whole}${fraction ? `.${fraction}` : ''}`
}

/**
 * A stored quantity as a person should see it.
 *
 * The column is `NUMERIC(20, 4)`, so the API sends "10.0000". Showing that
 * to somebody counting boxes is showing them the database: a clinic says
 * ten boxes, and a screen that says 10.0000 invites them to wonder what the
 * four decimals are hiding.
 */
export function displayQuantity(value: string | null | undefined): string {
  return formatQuantity(parseQuantity(value))
}

export function addQuantities(a: string, b: string): string {
  return formatQuantity(parseQuantity(a) + parseQuantity(b))
}

export function subtractQuantities(a: string, b: string): string {
  return formatQuantity(parseQuantity(a) - parseQuantity(b))
}

/** -1, 0 or 1, exactly, with no floating point anywhere near it. */
export function compareQuantities(a: string, b: string): number {
  const left = parseQuantity(a)
  const right = parseQuantity(b)
  if (left < right) return -1
  if (left > right) return 1
  return 0
}

/** Which way a movement pushed the shelf. */
export function movementDirection(kind: StockMovementKind): 'in' | 'out' {
  return kind === 'RECEIPT' || kind === 'TRANSFER_IN' ? 'in' : 'out'
}

/**
 * An adjustment can push either way, so its direction comes from the sign
 * rather than from the kind.
 */
export function quantityDirection(quantity: string): 'in' | 'out' | 'none' {
  const sign = compareQuantities(quantity, '0')
  if (sign > 0) return 'in'
  if (sign < 0) return 'out'
  return 'none'
}

/** Total one item across every shelf and lot it sits on. */
export function totalOnHand(rows: readonly StockBalanceRow[], itemId: string): string {
  return rows
    .filter((row) => row.item_id === itemId)
    .reduce((running, row) => addQuantities(running, row.quantity), '0')
}

/** Roll per-lot, per-shelf rows up to one line per material. */
export function groupByItem(rows: readonly StockBalanceRow[]): {
  itemId: string
  code: string
  name: string
  unit: string
  quantity: string
  locations: number
}[] {
  const grouped = new Map<
    string,
    { itemId: string; code: string; name: string; unit: string; quantity: string; locations: Set<string> }
  >()
  for (const row of rows) {
    const existing = grouped.get(row.item_id)
    if (existing) {
      existing.quantity = addQuantities(existing.quantity, row.quantity)
      existing.locations.add(row.location_id)
      continue
    }
    grouped.set(row.item_id, {
      itemId: row.item_id,
      code: row.item_code,
      name: row.item_name,
      unit: row.unit,
      quantity: row.quantity,
      locations: new Set([row.location_id]),
    })
  }
  return [...grouped.values()]
    .map((entry) => ({
      itemId: entry.itemId,
      code: entry.code,
      name: entry.name,
      unit: entry.unit,
      quantity: entry.quantity,
      locations: entry.locations.size,
    }))
    .sort((a, b) => a.name.localeCompare(b.name))
}

/**
 * How urgent an expiry is.
 *
 * `expired` is past the date, `soon` is within a month, `watch` is anything
 * further out that was still asked for. The thresholds are the clinic's
 * working ones, not a standard: a month is the notice a dental practice
 * needs to use a lot up or order a replacement.
 */
export type ExpiryUrgency = 'expired' | 'soon' | 'watch'

export function expiryUrgency(daysRemaining: number): ExpiryUrgency {
  if (daysRemaining < 0) return 'expired'
  if (daysRemaining <= 30) return 'soon'
  return 'watch'
}

/** Expired lots first, then the soonest to go. */
export function sortByUrgency(rows: readonly ExpiringRow[]): ExpiringRow[] {
  return [...rows].sort((a, b) => a.days_remaining - b.days_remaining)
}

/**
 * What a count line would change, as the person typing it would read it.
 *
 * Positive means the shelf holds more than the ledger thought.
 */
export function countDifference(systemQuantity: string, countedQuantity: string): string {
  return subtractQuantities(countedQuantity, systemQuantity)
}

/** True when a counted figure matches the ledger and nothing would move. */
export function countAgrees(systemQuantity: string, countedQuantity: string): boolean {
  return compareQuantities(countedQuantity, systemQuantity) === 0
}

/**
 * Which moves a count offers, mirroring the backend's transitions narrowly
 * so the screen never offers a button the server is going to refuse.
 */
export const COUNT_TRANSITIONS: Record<AdjustmentStatus, readonly AdjustmentStatus[]> = {
  DRAFT: ['PROPOSED'],
  PROPOSED: ['APPROVED', 'REJECTED'],
  APPROVED: [],
  REJECTED: [],
}

export function nextCountStatuses(status: AdjustmentStatus): readonly AdjustmentStatus[] {
  return COUNT_TRANSITIONS[status] ?? []
}

export function countIsFinished(status: AdjustmentStatus): boolean {
  return nextCountStatuses(status).length === 0
}

/**
 * May this person approve this count?
 *
 * The permission is not enough: whoever counted the shelf may not be the
 * one who agrees the difference, and in a clinic of three one person will
 * often hold both permissions. The server refuses regardless; this stops
 * the button being offered in the first place.
 */
export function mayApproveCount(
  count: { status: AdjustmentStatus; counted_by_user_id: string | null },
  viewerUserId: string | null,
  hasApprovePermission: boolean,
): boolean {
  if (!hasApprovePermission) return false
  if (count.status !== 'PROPOSED') return false
  return !(count.counted_by_user_id !== null && count.counted_by_user_id === viewerUserId)
}

/**
 * Would taking this much leave the shelf short?
 *
 * The server is the authority and refuses under a lock. This is only so the
 * form can say so before it sends.
 */
export function wouldGoNegative(available: string, requested: string): boolean {
  return compareQuantities(requested, available) > 0
}

/** True when the item needs a lot number before anything can move. */
export function needsBatch(item: { is_batch_tracked: boolean }, batchId: string | null): boolean {
  return item.is_batch_tracked && !batchId
}
