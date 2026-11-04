import { describe, expect, it } from 'vitest'
import {
  addQuantities,
  compareQuantities,
  countAgrees,
  countDifference,
  countIsFinished,
  displayQuantity,
  expiryUrgency,
  formatQuantity,
  groupByItem,
  mayApproveCount,
  movementDirection,
  needsBatch,
  nextCountStatuses,
  parseQuantity,
  parseQuantityInput,
  quantityDirection,
  sortByUrgency,
  subtractQuantities,
  totalOnHand,
  wouldGoNegative,
} from './inventory'
import type { ExpiringRow, StockBalanceRow } from './types'

function balance(overrides: Partial<StockBalanceRow> = {}): StockBalanceRow {
  return {
    item_id: 'item-1',
    item_code: 'COMP-A2',
    item_name: 'Composite A2',
    unit: 'box',
    location_id: 'loc-1',
    batch_id: null,
    batch_code: null,
    expires_on: null,
    quantity: '10.0000',
    ...overrides,
  }
}

function expiring(overrides: Partial<ExpiringRow> = {}): ExpiringRow {
  return {
    item_id: 'item-1',
    item_name: 'Lidocaine',
    unit: 'ampoule',
    batch_id: 'batch-1',
    batch_code: 'L-1',
    expires_on: '2026-12-01',
    quantity: '5.0000',
    days_remaining: 20,
    ...overrides,
  }
}

describe('quantity arithmetic', () => {
  it('keeps four decimal places, which money would truncate to two', () => {
    // An anaesthetic used by the millilitre is exactly the case that a
    // two-decimal helper would quietly round away.
    expect(formatQuantity(parseQuantity('0.0005'))).toBe('0.0005')
    expect(addQuantities('0.0005', '0.0005')).toBe('0.001')
  })

  it('adds without floating point error', () => {
    expect(addQuantities('0.1', '0.2')).toBe('0.3')
    expect(addQuantities('1.0001', '2.9999')).toBe('4')
  })

  it('subtracts past zero, because a difference may be negative', () => {
    expect(subtractQuantities('8', '10')).toBe('-2')
  })

  it('drops trailing zeros, because a clinic says ten boxes', () => {
    expect(formatQuantity(parseQuantity('10.0000'))).toBe('10')
    expect(formatQuantity(parseQuantity('10.5000'))).toBe('10.5')
  })

  it('is exact at a magnitude a JavaScript number is not', () => {
    expect(addQuantities('9007199254740993', '1')).toBe('9007199254740994')
  })

  it('compares without converting to a number', () => {
    expect(compareQuantities('10', '9')).toBe(1)
    expect(compareQuantities('9', '10')).toBe(-1)
    expect(compareQuantities('10.0000', '10')).toBe(0)
  })

  it('reads a typed quantity and refuses what is not one', () => {
    expect(parseQuantityInput('2,5')).toBe(25000n)
    expect(parseQuantityInput('')).toBe(0n)
    expect(parseQuantityInput('-1')).toBeNull()
    expect(parseQuantityInput('two')).toBeNull()
    expect(parseQuantityInput('1.23456')).toBeNull()
  })

  it('treats a missing quantity as nothing rather than throwing', () => {
    expect(parseQuantity(null)).toBe(0n)
    expect(parseQuantity(undefined)).toBe(0n)
    expect(parseQuantity('')).toBe(0n)
  })
})

describe('movement direction', () => {
  it('knows which way each kind pushes the shelf', () => {
    expect(movementDirection('RECEIPT')).toBe('in')
    expect(movementDirection('TRANSFER_IN')).toBe('in')
    expect(movementDirection('CONSUMPTION')).toBe('out')
    expect(movementDirection('WRITE_OFF')).toBe('out')
    expect(movementDirection('TRANSFER_OUT')).toBe('out')
  })

  it('reads an adjustment from its sign, because it can go either way', () => {
    expect(quantityDirection('2')).toBe('in')
    expect(quantityDirection('-2')).toBe('out')
    expect(quantityDirection('0')).toBe('none')
  })
})

describe('balances', () => {
  it('totals one material across every shelf and lot', () => {
    const rows = [
      balance({ quantity: '10' }),
      balance({ location_id: 'loc-2', quantity: '4.5' }),
      balance({ item_id: 'item-2', quantity: '99' }),
    ]
    expect(totalOnHand(rows, 'item-1')).toBe('14.5')
  })

  it('is zero for a material on no shelf', () => {
    expect(totalOnHand([balance()], 'nobody')).toBe('0')
  })

  it('groups per-lot rows into one line per material and counts shelves', () => {
    const rows = [
      balance({ quantity: '10', batch_code: 'A' }),
      balance({ quantity: '5', batch_code: 'B' }),
      balance({ location_id: 'loc-2', quantity: '2' }),
      balance({ item_id: 'item-2', item_name: 'Alginate', quantity: '1' }),
    ]
    const grouped = groupByItem(rows)
    expect(grouped.map((row) => row.name)).toEqual(['Alginate', 'Composite A2'])
    const composite = grouped.find((row) => row.itemId === 'item-1')
    expect(composite?.quantity).toBe('17')
    expect(composite?.locations).toBe(2)
  })
})

describe('expiry', () => {
  it('sorts expired lots first, then the soonest', () => {
    const rows = [
      expiring({ batch_code: 'later', days_remaining: 40 }),
      expiring({ batch_code: 'expired', days_remaining: -5 }),
      expiring({ batch_code: 'soon', days_remaining: 3 }),
    ]
    expect(sortByUrgency(rows).map((row) => row.batch_code)).toEqual([
      'expired',
      'soon',
      'later',
    ])
  })

  it('grades urgency by the notice a clinic actually needs', () => {
    expect(expiryUrgency(-1)).toBe('expired')
    expect(expiryUrgency(0)).toBe('soon')
    expect(expiryUrgency(30)).toBe('soon')
    expect(expiryUrgency(31)).toBe('watch')
  })

  it('does not mutate what it was given', () => {
    const rows = [expiring({ days_remaining: 40 }), expiring({ days_remaining: 1 })]
    sortByUrgency(rows)
    expect(rows[0]?.days_remaining).toBe(40)
  })
})

describe('the count', () => {
  it('shows the difference the way the person counting reads it', () => {
    expect(countDifference('10', '8')).toBe('-2')
    expect(countDifference('10', '12')).toBe('2')
  })

  it('knows when nothing would move', () => {
    expect(countAgrees('10', '10.0000')).toBe(true)
    expect(countAgrees('10', '9.9999')).toBe(false)
  })

  it('offers only the moves the backend would allow', () => {
    expect(nextCountStatuses('DRAFT')).toEqual(['PROPOSED'])
    expect(nextCountStatuses('PROPOSED')).toEqual(['APPROVED', 'REJECTED'])
    expect(nextCountStatuses('APPROVED')).toEqual([])
    expect(countIsFinished('REJECTED')).toBe(true)
    expect(countIsFinished('DRAFT')).toBe(false)
  })

  it('does not offer approval to whoever did the counting', () => {
    const count = { status: 'PROPOSED' as const, counted_by_user_id: 'user-1' }
    expect(mayApproveCount(count, 'user-1', true)).toBe(false)
    expect(mayApproveCount(count, 'user-2', true)).toBe(true)
  })

  it('does not offer approval without the permission', () => {
    const count = { status: 'PROPOSED' as const, counted_by_user_id: 'user-1' }
    expect(mayApproveCount(count, 'user-2', false)).toBe(false)
  })

  it('does not offer approval on a draft', () => {
    const count = { status: 'DRAFT' as const, counted_by_user_id: 'user-1' }
    expect(mayApproveCount(count, 'user-2', true)).toBe(false)
  })

  it('offers approval when nobody is recorded as having counted', () => {
    const count = { status: 'PROPOSED' as const, counted_by_user_id: null }
    expect(mayApproveCount(count, 'user-1', true)).toBe(true)
  })
})

describe('warnings before the server refuses', () => {
  it('spots a shelf that would go short', () => {
    expect(wouldGoNegative('2', '3')).toBe(true)
    expect(wouldGoNegative('2', '2')).toBe(false)
    expect(wouldGoNegative('2.0001', '2.0002')).toBe(true)
  })

  it('knows when a lot number is required first', () => {
    expect(needsBatch({ is_batch_tracked: true }, null)).toBe(true)
    expect(needsBatch({ is_batch_tracked: true }, 'batch-1')).toBe(false)
    expect(needsBatch({ is_batch_tracked: false }, null)).toBe(false)
  })
})

describe('showing a quantity to a person', () => {
  it('drops the database decimals a clinic never says', () => {
    // The column is NUMERIC(20, 4), so the API sends 10.0000 and the screen
    // must not. This is the defect a browser pass found: the shelf read
    // "10.0000 box" where the clinic counts ten boxes.
    expect(displayQuantity('10.0000')).toBe('10')
    expect(displayQuantity('2.5000')).toBe('2.5')
    expect(displayQuantity('0.0500')).toBe('0.05')
  })

  it('keeps a real fraction that matters', () => {
    expect(displayQuantity('0.0005')).toBe('0.0005')
  })

  it('shows a negative difference as one', () => {
    expect(displayQuantity('-2.0000')).toBe('-2')
  })

  it('survives a missing value rather than printing undefined', () => {
    expect(displayQuantity(null)).toBe('0')
    expect(displayQuantity(undefined)).toBe('0')
    expect(displayQuantity('')).toBe('0')
  })
})
