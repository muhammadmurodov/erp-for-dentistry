import { describe, expect, it } from 'vitest'
import {
  DECIDUOUS_QUADRANTS,
  isDeciduousTooth,
  isPermanentTooth,
  isValidTooth,
  parseSurfaces,
  parseTooth,
  PERMANENT_QUADRANTS,
  quadrantOf,
  sortTeeth,
} from './fdi'

describe('which numbers are teeth', () => {
  it('accepts every permanent tooth and no number between the quadrants', () => {
    for (const tooth of [11, 18, 21, 28, 31, 38, 41, 48]) {
      expect(isValidTooth(tooth), `${tooth}`).toBe(true)
    }
    // 19 and 29 do not exist: counting stops at 8 in each quadrant.
    for (const tooth of [10, 19, 20, 29, 30, 39, 40, 49]) {
      expect(isValidTooth(tooth), `${tooth}`).toBe(false)
    }
  })

  it('accepts milk teeth, because children are treated here', () => {
    for (const tooth of [51, 55, 61, 65, 71, 75, 81, 85]) {
      expect(isValidTooth(tooth), `${tooth}`).toBe(true)
    }
    for (const tooth of [50, 56, 66, 76, 86]) {
      expect(isValidTooth(tooth), `${tooth}`).toBe(false)
    }
  })

  it('tells the two dentitions apart', () => {
    expect(isPermanentTooth(16)).toBe(true)
    expect(isDeciduousTooth(16)).toBe(false)
    expect(isDeciduousTooth(55)).toBe(true)
    expect(isPermanentTooth(55)).toBe(false)
  })

  it('rejects nothing-at-all as a tooth', () => {
    expect(isValidTooth(null)).toBe(false)
    expect(isValidTooth(undefined)).toBe(false)
  })

  it('has 32 permanent and 20 deciduous teeth, which is how many there are', () => {
    expect(PERMANENT_QUADRANTS.flatMap((q) => q.teeth)).toHaveLength(32)
    expect(DECIDUOUS_QUADRANTS.flatMap((q) => q.teeth)).toHaveLength(20)
  })
})

describe('parsing what a dentist typed', () => {
  it('reads a valid tooth', () => {
    expect(parseTooth('24')).toEqual({ tooth: 24, error: null })
    expect(parseTooth('  36 ')).toEqual({ tooth: 36, error: null })
  })

  it('treats an empty box as "no particular tooth", not an error', () => {
    // A consultation or a scale and polish belongs to no single tooth, and
    // demanding one would get one invented.
    expect(parseTooth('')).toEqual({ tooth: null, error: null })
    expect(parseTooth('   ')).toEqual({ tooth: null, error: null })
  })

  it('refuses a number that is not a tooth', () => {
    expect(parseTooth('19').error).toBe('toothOutOfRange')
    expect(parseTooth('99').error).toBe('toothOutOfRange')
    expect(parseTooth('0').error).toBe('toothOutOfRange')
  })

  it('refuses something that is not a number at all', () => {
    expect(parseTooth('abc').error).toBe('toothNotANumber')
    expect(parseTooth('2.4').error).toBe('toothNotANumber')
    expect(parseTooth('-24').error).toBe('toothNotANumber')
  })
})

describe('tooth surfaces', () => {
  it('upper-cases what was typed', () => {
    expect(parseSurfaces('mo')).toEqual({ surfaces: 'MO', error: null })
  })

  it('keeps the order the dentist wrote', () => {
    expect(parseSurfaces('OM').surfaces).toBe('OM')
    expect(parseSurfaces('MO').surfaces).toBe('MO')
  })

  it('drops a repeated surface rather than storing it twice', () => {
    expect(parseSurfaces('MOM').surfaces).toBe('MO')
  })

  it('ignores separators somebody may type', () => {
    expect(parseSurfaces('M, O, D').surfaces).toBe('MOD')
    expect(parseSurfaces('M O').surfaces).toBe('MO')
  })

  it('accepts an empty box', () => {
    expect(parseSurfaces('')).toEqual({ surfaces: null, error: null })
  })

  it('refuses a letter that is not a surface', () => {
    expect(parseSurfaces('MZ').error).toBe('surfacesInvalid')
    expect(parseSurfaces('123').error).toBe('surfacesInvalid')
  })

  it('accepts both spellings the register uses for the cheek and tongue sides', () => {
    // B/V and L/P are the same two surfaces under different conventions, and
    // this clinic's register uses both.
    for (const code of ['B', 'V', 'L', 'P', 'I', 'M', 'O', 'D']) {
      expect(parseSurfaces(code).error, code).toBeNull()
    }
  })
})

describe('grouping a mouth', () => {
  it('puts each tooth in its quadrant', () => {
    expect(quadrantOf(16)).toBe('upper-right')
    expect(quadrantOf(26)).toBe('upper-left')
    expect(quadrantOf(36)).toBe('lower-left')
    expect(quadrantOf(46)).toBe('lower-right')
  })

  it('puts milk teeth in the same quadrants as their successors', () => {
    expect(quadrantOf(55)).toBe('upper-right')
    expect(quadrantOf(65)).toBe('upper-left')
    expect(quadrantOf(75)).toBe('lower-left')
    expect(quadrantOf(85)).toBe('lower-right')
  })

  it('has no quadrant for a number that is not a tooth', () => {
    expect(quadrantOf(19)).toBeNull()
  })

  it('sorts teeth the way a chart reads, not the way numbers sort', () => {
    // 18 comes before 11 on the upper right: a chart counts outwards from
    // the midline, so sorting numerically would read the mouth backwards.
    expect(sortTeeth([11, 18, 14])).toEqual([18, 14, 11])
    expect(sortTeeth([21, 28, 24])).toEqual([21, 24, 28])
  })

  it('puts permanent teeth before milk teeth', () => {
    expect(sortTeeth([55, 16])).toEqual([16, 55])
  })
})
