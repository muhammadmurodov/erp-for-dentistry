/**
 * Teeth, in the notation the clinic's paper register already uses.
 *
 * FDI two-digit notation: the first digit is the quadrant, the second the
 * tooth within it, counting outwards from the midline. Permanent teeth are
 * 11–18, 21–28, 31–38, 41–48; deciduous (milk) teeth are 51–55, 61–65,
 * 71–75, 81–85. Every tooth number in the clinic's register falls in the
 * permanent ranges, but children are treated here, so the deciduous ranges
 * are accepted too.
 *
 * The backend validates the same ranges and is the authority. This exists so
 * a dentist finds out at the keystroke rather than after a failed save, and
 * so the grid can be laid out in anatomical order rather than numeric order —
 * the upper right quadrant reads 18 → 11, because that is how a mouth is
 * written down.
 */

/** Quadrants in the order they are drawn on a chart, and their teeth. */
export const PERMANENT_QUADRANTS = [
  { key: 'upper-right', teeth: [18, 17, 16, 15, 14, 13, 12, 11] },
  { key: 'upper-left', teeth: [21, 22, 23, 24, 25, 26, 27, 28] },
  { key: 'lower-right', teeth: [48, 47, 46, 45, 44, 43, 42, 41] },
  { key: 'lower-left', teeth: [31, 32, 33, 34, 35, 36, 37, 38] },
] as const

export const DECIDUOUS_QUADRANTS = [
  { key: 'upper-right', teeth: [55, 54, 53, 52, 51] },
  { key: 'upper-left', teeth: [61, 62, 63, 64, 65] },
  { key: 'lower-right', teeth: [85, 84, 83, 82, 81] },
  { key: 'lower-left', teeth: [71, 72, 73, 74, 75] },
] as const

const PERMANENT = new Set(PERMANENT_QUADRANTS.flatMap((q) => q.teeth as readonly number[]))
const DECIDUOUS = new Set(DECIDUOUS_QUADRANTS.flatMap((q) => q.teeth as readonly number[]))

export function isPermanentTooth(tooth: number): boolean {
  return PERMANENT.has(tooth)
}

export function isDeciduousTooth(tooth: number): boolean {
  return DECIDUOUS.has(tooth)
}

/** Is this a tooth the system will accept? */
export function isValidTooth(tooth: number | null | undefined): boolean {
  if (tooth === null || tooth === undefined) return false
  return PERMANENT.has(tooth) || DECIDUOUS.has(tooth)
}

/**
 * Parse what somebody typed into a tooth number.
 *
 * Returns `null` for an empty box, which is not an error: plenty of work —
 * a consultation, a scale and polish — belongs to no single tooth, and
 * demanding one would get one invented.
 */
export function parseTooth(raw: string): { tooth: number | null; error: ErrorKey | null } {
  const text = raw.trim()
  if (text === '') return { tooth: null, error: null }
  if (!/^\d+$/.test(text)) return { tooth: null, error: 'toothNotANumber' }
  const tooth = Number(text)
  if (!isValidTooth(tooth)) return { tooth: null, error: 'toothOutOfRange' }
  return { tooth, error: null }
}

export type ErrorKey = 'toothNotANumber' | 'toothOutOfRange' | 'surfacesInvalid'

/**
 * Tooth surfaces, as the register writes them: M, O, D, B/V, L/P, I.
 *
 * Upper-cased and de-duplicated, order preserved, because `om` and `MO` are
 * the same filling and storing both spellings makes two records of one thing.
 */
const SURFACES = new Set(['M', 'O', 'D', 'B', 'V', 'L', 'P', 'I'])

export function parseSurfaces(raw: string): {
  surfaces: string | null
  error: ErrorKey | null
} {
  const text = raw.trim().toUpperCase().replace(/[\s,]+/g, '')
  if (text === '') return { surfaces: null, error: null }
  const seen: string[] = []
  for (const character of text) {
    if (!SURFACES.has(character)) return { surfaces: null, error: 'surfacesInvalid' }
    if (!seen.includes(character)) seen.push(character)
  }
  return { surfaces: seen.join(''), error: null }
}

/** Which quadrant a tooth belongs to, for grouping a patient's history. */
export function quadrantOf(tooth: number): string | null {
  if (!isValidTooth(tooth)) return null
  const first = Math.floor(tooth / 10)
  if (first === 1 || first === 5) return 'upper-right'
  if (first === 2 || first === 6) return 'upper-left'
  if (first === 3 || first === 7) return 'lower-left'
  return 'lower-right'
}

/**
 * Teeth in the order a chart reads, so a patient's history groups the way a
 * dentist expects rather than the way numbers sort.
 */
export function sortTeeth(teeth: number[]): number[] {
  const order = [
    ...PERMANENT_QUADRANTS.flatMap((q) => q.teeth as readonly number[]),
    ...DECIDUOUS_QUADRANTS.flatMap((q) => q.teeth as readonly number[]),
  ]
  return [...teeth].sort((a, b) => order.indexOf(a) - order.indexOf(b))
}
