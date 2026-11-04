import { describe, expect, it } from 'vitest'
import {
  billIsEditable,
  billIsPayable,
  fromMinorUnits,
  lineTotal,
  nextOrderStatuses,
  orderAwaitsDelivery,
  orderIsFinished,
  orderMoveNeedsReason,
  sumAmounts,
  toMinorUnits,
} from './purchasing'
import type { OrderStatus } from './types'

describe('order transitions', () => {
  it('walks a draft through approval and sending', () => {
    expect(nextOrderStatuses('DRAFT')).toContain('APPROVED')
    expect(nextOrderStatuses('APPROVED')).toContain('SENT')
  })

  it('never offers a jump from draft to received', () => {
    // Receiving happens through a delivery, not a button.
    expect(nextOrderStatuses('DRAFT')).not.toContain('RECEIVED')
  })

  it('offers nothing once an order is received or cancelled', () => {
    expect(orderIsFinished('RECEIVED')).toBe(true)
    expect(orderIsFinished('CANCELLED')).toBe(true)
    expect(orderIsFinished('CLOSED')).toBe(true)
  })

  it('lets a part-delivered order be closed short', () => {
    expect(nextOrderStatuses('PARTIALLY_RECEIVED')).toEqual(['CLOSED'])
  })

  it('knows which orders are still expecting a delivery', () => {
    for (const status of ['APPROVED', 'SENT', 'PARTIALLY_RECEIVED'] as OrderStatus[]) {
      expect(orderAwaitsDelivery(status)).toBe(true)
    }
    for (const status of ['DRAFT', 'RECEIVED', 'CANCELLED', 'CLOSED'] as OrderStatus[]) {
      expect(orderAwaitsDelivery(status)).toBe(false)
    }
  })

  it('demands a reason only for closing short', () => {
    expect(orderMoveNeedsReason('CLOSED')).toBe(true)
    expect(orderMoveNeedsReason('CANCELLED')).toBe(false)
  })
})

describe('bills', () => {
  it('is payable only when registered and something is left', () => {
    expect(billIsPayable({ status: 'REGISTERED', remaining: '500000.00' })).toBe(true)
    expect(billIsPayable({ status: 'REGISTERED', remaining: '0.00' })).toBe(false)
    expect(billIsPayable({ status: 'DRAFT', remaining: '500000.00' })).toBe(false)
    expect(billIsPayable({ status: 'VOID', remaining: '500000.00' })).toBe(false)
  })

  it('is editable only as a draft', () => {
    expect(billIsEditable('DRAFT')).toBe(true)
    expect(billIsEditable('REGISTERED')).toBe(false)
    expect(billIsEditable('VOID')).toBe(false)
  })
})

describe('money arithmetic', () => {
  it('never goes through a binary float', () => {
    // 0.1 + 0.2 !== 0.3 as doubles; these are exact.
    expect(sumAmounts(['0.10', '0.20'])).toBe('0.30')
  })

  it('keeps a large som amount exact', () => {
    // Beyond 2^53 a double would round this.
    expect(sumAmounts(['9007199254740993.00', '1.00'])).toBe('9007199254740994.00')
  })

  it('multiplies a quantity by a price exactly', () => {
    expect(lineTotal('10', '50000')).toBe('500000.00')
    expect(lineTotal('2.5', '1000')).toBe('2500.00')
    expect(lineTotal('3', '0.33')).toBe('0.99')
  })

  it('reports nothing for an unparseable amount rather than NaN', () => {
    expect(lineTotal('', '1000')).toBe('0.00')
    expect(lineTotal('abc', '1000')).toBe('0.00')
    expect(sumAmounts(['abc', '5.00'])).toBe('5.00')
  })

  it('always shows at least two decimal places', () => {
    expect(sumAmounts(['5'])).toBe('5.00')
    expect(fromMinorUnits(500n, 2)).toBe('5.00')
  })

  it('keeps more than two places when they are real', () => {
    expect(fromMinorUnits(123456n, 4)).toBe('12.3456')
  })

  it('handles a negative amount', () => {
    expect(fromMinorUnits(-500n, 2)).toBe('-5.00')
    expect(toMinorUnits('-1.25', 2)).toBe(-125n)
  })

  it('parses and rejects', () => {
    expect(toMinorUnits('12.34', 2)).toBe(1234n)
    expect(toMinorUnits('12', 2)).toBe(1200n)
    expect(toMinorUnits('12.3456', 2)).toBe(1234n)
    expect(toMinorUnits('', 2)).toBeNull()
    expect(toMinorUnits('.', 2)).toBeNull()
    expect(toMinorUnits('1,5', 2)).toBeNull()
  })
})
