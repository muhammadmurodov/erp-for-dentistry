import { describe, expect, it } from 'vitest'
import {
  clampToZero,
  computeRemaining,
  formatAmount,
  parseAmount,
  parseInputAmount,
} from './money'

/**
 * The specification's four scenarios, on the frontend side of the wire.
 *
 * The backend has the same cases asserted against the ledger. These assert
 * the arithmetic the panel does while the cashier is still typing — which is
 * the only arithmetic the browser does on money, and therefore the only place
 * a float could do damage.
 */

const TREATMENT = '1000000.00'

function remaining(
  over: Partial<Parameters<typeof computeRemaining>[0]> = {},
) {
  return computeRemaining({
    total: TREATMENT,
    paid: '0.00',
    cash: '',
    card: '',
    advances: '',
    advancesAvailable: '0.00',
    ...over,
  })
}

describe('parsing what a cashier types', () => {
  it('reads an empty field as zero', () => {
    expect(parseInputAmount('')).toBe(0n)
    expect(parseInputAmount('   ')).toBe(0n)
  })

  it('reads whole soʼm and minor units', () => {
    expect(parseInputAmount('1000000')).toBe(100000000n)
    expect(parseInputAmount('400000.50')).toBe(40000050n)
    expect(parseInputAmount('0.05')).toBe(5n)
  })

  it('accepts a comma, which is what a Uzbek keyboard offers', () => {
    expect(parseInputAmount('400000,50')).toBe(40000050n)
  })

  it('refuses a negative amount', () => {
    // A refund is a different operation with a different permission, not a
    // minus sign in the cash field.
    expect(parseInputAmount('-100000')).toBeNull()
  })

  it('refuses anything that is not an amount', () => {
    expect(parseInputAmount('abc')).toBeNull()
    expect(parseInputAmount('1e6')).toBeNull()
    expect(parseInputAmount('100.123')).toBeNull()
  })
})

describe('parsing what the API sent', () => {
  it('keeps the exact value of a large amount', () => {
    // 9 007 199 254 740 993 minor units is beyond Number.MAX_SAFE_INTEGER;
    // a float would have lost the last digit.
    expect(parseAmount('90071992547409.93')).toBe(9007199254740993n)
  })

  it('truncates the storage scale without rounding', () => {
    expect(parseAmount('300000.0000')).toBe(30000000n)
  })

  it('reads a negative balance', () => {
    expect(parseAmount('-250000.00')).toBe(-25000000n)
  })

  it('round-trips through formatting', () => {
    for (const value of ['0.00', '1000000.00', '400000.50', '-99.99']) {
      expect(formatAmount(parseAmount(value))).toBe(value)
    }
  })
})

describe('scenario 1 — paid in full in cash', () => {
  it('shows nothing remaining and settles the bill', () => {
    const result = remaining({ cash: '1000000' })
    expect(formatAmount(result.remaining)).toBe('0.00')
    expect(result.settlesInFull).toBe(true)
    expect(result.overpays).toBe(false)
  })
})

describe('scenario 2 — paid in full by card', () => {
  it('shows nothing remaining and settles the bill', () => {
    const result = remaining({ card: '1000000' })
    expect(formatAmount(result.remaining)).toBe('0.00')
    expect(result.settlesInFull).toBe(true)
  })
})

describe('scenario 3 — mixed cash and card', () => {
  it('counts both halves against one bill', () => {
    const result = remaining({ cash: '400000', card: '600000' })
    expect(formatAmount(result.remaining)).toBe('0.00')
    expect(result.settlesInFull).toBe(true)
  })

  it('refuses the pair when each half alone would fit', () => {
    const result = remaining({ cash: '600000', card: '600000' })
    expect(result.overpays).toBe(true)
    expect(formatAmount(result.remaining)).toBe('0.00')
  })
})

describe('scenario 4 — a partial payment', () => {
  it('leaves the rest as the remaining balance', () => {
    const result = remaining({ cash: '300000', card: '200000' })
    expect(formatAmount(result.remaining)).toBe('500000.00')
    expect(result.settlesInFull).toBe(false)
    expect(result.overpays).toBe(false)
  })

  it('counts money already paid as well as money being taken', () => {
    const result = remaining({ paid: '300000.00', cash: '200000' })
    expect(formatAmount(result.remainingBeforeDraft)).toBe('700000.00')
    expect(formatAmount(result.remaining)).toBe('500000.00')
  })
})

describe('applying an advance', () => {
  it('reduces the remaining balance like any other confirmed money', () => {
    const result = remaining({ advances: '400000', advancesAvailable: '400000.00' })
    expect(formatAmount(result.remaining)).toBe('600000.00')
    expect(result.advanceExceedsAvailable).toBe(false)
  })

  it('flags an advance larger than the patient has', () => {
    const result = remaining({ advances: '400000', advancesAvailable: '100000.00' })
    expect(result.advanceExceedsAvailable).toBe(true)
  })

  it('combines with cash and card in one submission', () => {
    const result = remaining({
      advances: '200000',
      advancesAvailable: '200000.00',
      cash: '300000',
      card: '500000',
    })
    expect(formatAmount(result.remaining)).toBe('0.00')
    expect(result.settlesInFull).toBe(true)
  })
})

describe('the remaining balance never goes negative', () => {
  it('floors at zero however much is typed', () => {
    const result = remaining({ cash: '5000000' })
    expect(result.remaining).toBe(0n)
    expect(result.overpays).toBe(true)
  })

  it('stays at zero on an already settled bill', () => {
    const result = remaining({ paid: '1000000.00' })
    expect(result.remaining).toBe(0n)
    expect(result.remainingBeforeDraft).toBe(0n)
  })

  it('floors a paid figure that somehow exceeds the total', () => {
    const result = remaining({ total: '100.00', paid: '150.00' })
    expect(result.remainingBeforeDraft).toBe(0n)
  })
})

describe('a field that is not an amount', () => {
  it('is reported rather than silently treated as zero', () => {
    const result = remaining({ cash: 'four hundred' })
    expect(result.invalid).toBe(true)
    expect(result.overpays).toBe(false)
    expect(result.settlesInFull).toBe(false)
  })
})

describe('arithmetic that a float would get wrong', () => {
  it('subtracts a fractional amount exactly', () => {
    // 1000000.10 - 0.10 in doubles is 1000000.0999999999
    const result = remaining({ total: '1000000.10', cash: '0.10' })
    expect(formatAmount(result.remaining)).toBe('1000000.00')
  })

  it('adds a hundred five-tiyin payments without drift', () => {
    let total = 0n
    for (let i = 0; i < 100; i += 1) total += parseInputAmount('0.05') ?? 0n
    expect(formatAmount(total)).toBe('5.00')
  })
})

describe('clampToZero', () => {
  it('passes a positive amount through and floors a negative one', () => {
    expect(clampToZero(5n)).toBe(5n)
    expect(clampToZero(-5n)).toBe(0n)
  })
})
