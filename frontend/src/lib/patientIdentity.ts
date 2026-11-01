/**
 * Comparing two patient records that might be one person.
 *
 * This clinic has never had a patient number. The paper register identifies
 * people by family name and birth year, so two records that look alike are
 * the normal case — and the only way to decide is to put the identifying
 * fields side by side and look at them.
 *
 * Pure, and tested, because the arithmetic of "do these agree" is where a
 * comparison screen quietly misleads: a blank telephone number is not a
 * disagreement, `+998 90 123 45 67` and `+998901234567` are the same number,
 * and `Oripov` and `oripov ` are the same surname. Getting any of those wrong
 * either hides a real difference or invents one, and somebody merges two
 * people's dental histories on the strength of it.
 */

/** The identifying fields, in the order reception reads them aloud. */
export const IDENTITY_FIELDS = [
  'family_name',
  'given_name',
  'patronymic',
  'birth_year',
  'phone',
  'address',
] as const

export type IdentityField = (typeof IDENTITY_FIELDS)[number]

/** Just enough of a patient to compare. */
export interface ComparablePatient {
  family_name: string
  given_name: string
  patronymic: string | null
  birth_year: number | null
  phone: string | null
  address: string | null
}

export type Agreement =
  /** Both records say the same thing. */
  | 'same'
  /** Both say something, and the two differ. */
  | 'different'
  /** One is blank, so there is nothing to disagree with. */
  | 'missing'

export interface FieldComparison {
  field: IdentityField
  left: string | null
  right: string | null
  agreement: Agreement
}

/** Trim, collapse inner whitespace, drop case. Never `''` — blank is `null`. */
function normalizeText(value: string | null | undefined): string | null {
  if (value === null || value === undefined) return null
  const collapsed = value.trim().replace(/\s+/g, ' ')
  return collapsed === '' ? null : collapsed.toLocaleLowerCase('uz')
}

/**
 * Digits only, so formatting cannot pass for a difference.
 *
 * Deliberately not clever about country codes: `998901234567` and
 * `901234567` are left as different, because treating a nine-digit number as
 * the same as a twelve-digit one is a guess, and the backend's own duplicate
 * detection normalizes before it matches. A screen that disagreed with the
 * detector would be worse than one that says "different, look again".
 */
function normalizePhone(value: string | null | undefined): string | null {
  if (value === null || value === undefined) return null
  const digits = value.replace(/\D/g, '')
  return digits === '' ? null : digits
}

function displayValue(
  patient: ComparablePatient,
  field: IdentityField,
): string | null {
  const raw = patient[field]
  if (raw === null || raw === undefined) return null
  if (typeof raw === 'number') return String(raw)
  const trimmed = raw.trim()
  return trimmed === '' ? null : trimmed
}

function comparableValue(
  patient: ComparablePatient,
  field: IdentityField,
): string | null {
  if (field === 'phone') return normalizePhone(patient.phone)
  if (field === 'birth_year') {
    return patient.birth_year === null ? null : String(patient.birth_year)
  }
  return normalizeText(patient[field] as string | null)
}

/**
 * The two records, field by field.
 *
 * Returns every field, including the ones that agree: a comparison that
 * showed only the differences would leave the reader unable to tell a record
 * with one typo from a record about somebody else entirely.
 */
export function compareIdentities(
  left: ComparablePatient,
  right: ComparablePatient,
): FieldComparison[] {
  return IDENTITY_FIELDS.map((field) => {
    const a = comparableValue(left, field)
    const b = comparableValue(right, field)
    let agreement: Agreement
    if (a === null || b === null) {
      agreement = a === null && b === null ? 'same' : 'missing'
    } else {
      agreement = a === b ? 'same' : 'different'
    }
    return {
      field,
      left: displayValue(left, field),
      right: displayValue(right, field),
      agreement,
    }
  })
}

/** How many fields actively disagree. A blank on one side is not a conflict. */
export function countDifferences(rows: FieldComparison[]): number {
  return rows.filter((row) => row.agreement === 'different').length
}

/**
 * Whether anything positively contradicts these being one person.
 *
 * Used only to colour a hint on the screen. It is never a decision: the whole
 * design of this queue is that a person decides, with a reason, and nothing
 * merges because two strings happened to match (ADR-041).
 */
export function hasContradiction(rows: FieldComparison[]): boolean {
  return countDifferences(rows) > 0
}
