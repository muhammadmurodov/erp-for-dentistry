/**
 * Exact decimal arithmetic on money, in the browser.
 *
 * Amounts arrive from the API as decimal **strings** and are displayed as
 * text, which is enough for every screen that only shows figures. The payment
 * panel is different: it has to subtract what the cashier is typing from the
 * invoice total as they type it, and show the result. That is arithmetic, and
 * doing it in `number` would be a mistake — a JavaScript number is an
 * IEEE-754 double, and 1 000 000.10 - 0.10 does not reliably come back as
 * 1 000 000 in binary floating point. On a bill of ten million so'm the error
 * is invisible on screen and real in the ledger.
 *
 * So everything here works in **minor units as `bigint`**: 1 000 000.00 UZS
 * is `100000000n`. Exact, unbounded, and no rounding anywhere.
 *
 * The backend remains the authority. These helpers decide what the panel
 * shows and which inputs it disables; the remaining balance that matters is
 * recomputed server-side under the invoice's row lock, and a submission that
 * disagrees with it is refused.
 */

/** Minor units per major unit. The som is presented at two decimals. */
const SCALE = 2
const FACTOR = 100n

/** `0` in minor units, for callers that would otherwise write `0n`. */
export const ZERO = 0n

/**
 * Parses an amount a *user typed*: digits, optionally a decimal point and up
 * to two more digits. An empty field is zero.
 *
 * Returns `null` for anything else, including a negative number — taking a
 * negative payment is a refund, which is a different operation with a
 * different permission, not a minus sign in the cash box.
 */
export function parseInputAmount(input: string): bigint | null {
  const trimmed = input.trim().replace(/[\s ]/g, '')
  if (trimmed === '') return ZERO
  if (!/^\d+([.,]\d{0,2})?$/.test(trimmed)) return null
  const [whole = '0', fraction = ''] = trimmed.replace(',', '.').split('.')
  return BigInt(whole) * FACTOR + BigInt(fraction.padEnd(SCALE, '0'))
}

/**
 * Parses an amount the *API sent*: a decimal string, possibly negative, and
 * possibly carrying more decimals than are displayed, because the columns are
 * NUMERIC(20, 4). Extra digits are truncated rather than rounded, which
 * cannot happen in practice: every amount is quantized to the currency's
 * scale before it is stored.
 */
export function parseAmount(value: string | null | undefined): bigint {
  if (value === null || value === undefined || value === '') return ZERO
  const negative = value.trimStart().startsWith('-')
  const unsigned = negative ? value.trim().slice(1) : value.trim()
  if (!/^\d+(\.\d+)?$/.test(unsigned)) return ZERO
  const [whole = '0', fraction = ''] = unsigned.split('.')
  const minor =
    BigInt(whole) * FACTOR + BigInt(fraction.slice(0, SCALE).padEnd(SCALE, '0'))
  return negative ? -minor : minor
}

/** Renders minor units back as a decimal string, always with two decimals. */
export function formatAmount(minor: bigint): string {
  const negative = minor < ZERO
  const unsigned = negative ? -minor : minor
  const whole = unsigned / FACTOR
  const fraction = (unsigned % FACTOR).toString().padStart(SCALE, '0')
  return `${negative ? '-' : ''}${whole}.${fraction}`
}

/** Never below zero: a remaining balance is an amount owed, or nothing. */
export function clampToZero(minor: bigint): bigint {
  return minor > ZERO ? minor : ZERO
}

export interface RemainingInput {
  /** The invoice total, as the API sent it. */
  total: string
  /** Confirmed money already on the invoice, as the API sent it. */
  paid: string
  /** What the cashier has typed, as typed. */
  cash: string
  card: string
  /** How much of the patient's advance they have chosen to apply. */
  advances: string
  /** The advance actually available, as the API sent it. */
  advancesAvailable: string
}

export interface RemainingResult {
  /** What is still owed once the draft amounts are counted. Never negative. */
  remaining: bigint
  /** The remaining balance ignoring what is being typed. */
  remainingBeforeDraft: bigint
  /** Cash + card + advance, as typed. */
  draftTotal: bigint
  /** True when a field holds something that is not an amount. */
  invalid: boolean
  /** True when the draft exceeds what is owed — the backend would refuse it. */
  overpays: boolean
  /** True when the draft advance exceeds what the patient actually has. */
  advanceExceedsAvailable: boolean
  /** True when the draft would settle the bill exactly. */
  settlesInFull: boolean
}

/**
 * The remaining balance, recomputed from scratch on every keystroke.
 *
 * Deliberately a pure function of the invoice and the three inputs, with no
 * state of its own: there is nowhere for a stale figure to be kept, and no
 * way for the panel to show a balance that is merely the last one it was
 * told. The specification requires the field to be read-only, and this is
 * what makes that more than a UI attribute — the value does not exist
 * anywhere it could be edited.
 */
export function computeRemaining(input: RemainingInput): RemainingResult {
  const total = parseAmount(input.total)
  const paid = parseAmount(input.paid)
  const available = parseAmount(input.advancesAvailable)

  const cash = parseInputAmount(input.cash)
  const card = parseInputAmount(input.card)
  const advances = parseInputAmount(input.advances)
  const invalid = cash === null || card === null || advances === null

  const draftTotal = (cash ?? ZERO) + (card ?? ZERO) + (advances ?? ZERO)
  const remainingBeforeDraft = clampToZero(total - paid)
  const remaining = clampToZero(remainingBeforeDraft - draftTotal)

  return {
    remaining,
    remainingBeforeDraft,
    draftTotal,
    invalid,
    overpays: !invalid && draftTotal > remainingBeforeDraft,
    advanceExceedsAvailable: !invalid && (advances ?? ZERO) > available,
    settlesInFull:
      !invalid && draftTotal > ZERO && draftTotal === remainingBeforeDraft,
  }
}
