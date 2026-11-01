import { describe, expect, it } from 'vitest'
import {
  compareIdentities,
  countDifferences,
  hasContradiction,
  IDENTITY_FIELDS,
  type ComparablePatient,
} from './patientIdentity'

function patient(overrides: Partial<ComparablePatient> = {}): ComparablePatient {
  return {
    family_name: 'Oripov',
    given_name: 'Jahongir',
    patronymic: 'Alisher o‘g‘li',
    birth_year: 1990,
    phone: '+998901234567',
    address: 'Chilonzor 12',
    ...overrides,
  }
}

function rowFor(field: string, left: ComparablePatient, right: ComparablePatient) {
  const row = compareIdentities(left, right).find((r) => r.field === field)
  if (!row) throw new Error(`no comparison row for ${field}`)
  return row
}

describe('compareIdentities', () => {
  it('returns every identifying field, agreeing or not', () => {
    const rows = compareIdentities(patient(), patient())
    expect(rows.map((r) => r.field)).toEqual([...IDENTITY_FIELDS])
    expect(rows.every((r) => r.agreement === 'same')).toBe(true)
  })

  it('reports two identical records as having no differences', () => {
    const rows = compareIdentities(patient(), patient())
    expect(countDifferences(rows)).toBe(0)
    expect(hasContradiction(rows)).toBe(false)
  })

  it('spots a different surname', () => {
    const row = rowFor('family_name', patient(), patient({ family_name: 'Karimov' }))
    expect(row.agreement).toBe('different')
    expect(row.left).toBe('Oripov')
    expect(row.right).toBe('Karimov')
  })

  it('spots a different year of birth', () => {
    const row = rowFor('birth_year', patient(), patient({ birth_year: 1974 }))
    expect(row.agreement).toBe('different')
    expect(row.left).toBe('1990')
    expect(row.right).toBe('1974')
  })
})

describe('what must not count as a difference', () => {
  it('ignores surrounding whitespace', () => {
    expect(rowFor('family_name', patient(), patient({ family_name: '  Oripov ' })).agreement).toBe(
      'same',
    )
  })

  it('ignores case', () => {
    expect(rowFor('family_name', patient(), patient({ family_name: 'ORIPOV' })).agreement).toBe(
      'same',
    )
  })

  it('collapses repeated spaces inside a name', () => {
    expect(
      rowFor('given_name', patient({ given_name: 'Abdulla  Aziz' }), patient({ given_name: 'Abdulla Aziz' }))
        .agreement,
    ).toBe('same')
  })

  it('ignores telephone formatting', () => {
    expect(
      rowFor('phone', patient({ phone: '+998 90 123 45 67' }), patient({ phone: '+998901234567' }))
        .agreement,
    ).toBe('same')
  })

  it('ignores dashes and brackets in a telephone number', () => {
    expect(
      rowFor('phone', patient({ phone: '(998) 90-123-45-67' }), patient({ phone: '998901234567' }))
        .agreement,
    ).toBe('same')
  })
})

describe('a blank is not a disagreement', () => {
  it('calls one missing telephone number "missing", not "different"', () => {
    const row = rowFor('phone', patient(), patient({ phone: null }))
    expect(row.agreement).toBe('missing')
    expect(row.right).toBeNull()
  })

  it('treats an empty string as missing, not as a value', () => {
    const row = rowFor('address', patient(), patient({ address: '   ' }))
    expect(row.agreement).toBe('missing')
    expect(row.right).toBeNull()
  })

  it('treats both sides blank as agreement — there is nothing in dispute', () => {
    const row = rowFor(
      'patronymic',
      patient({ patronymic: null }),
      patient({ patronymic: null }),
    )
    expect(row.agreement).toBe('same')
  })

  it('does not count a missing field towards the difference tally', () => {
    const rows = compareIdentities(patient(), patient({ phone: null, address: null }))
    expect(countDifferences(rows)).toBe(0)
    expect(hasContradiction(rows)).toBe(false)
  })

  it('a missing year of birth is missing, not a mismatch', () => {
    const row = rowFor('birth_year', patient(), patient({ birth_year: null }))
    expect(row.agreement).toBe('missing')
  })
})

describe('the realistic cases from the paper register', () => {
  it('two brothers: same surname and year, different given name', () => {
    const rows = compareIdentities(
      patient({ given_name: 'Jahongir' }),
      patient({ given_name: 'Bahodir', phone: null }),
    )
    expect(countDifferences(rows)).toBe(1)
    expect(rowFor('family_name', patient(), patient()).agreement).toBe('same')
  })

  it('the same person typed twice: one record simply has less in it', () => {
    const rows = compareIdentities(
      patient(),
      patient({ patronymic: null, address: null, phone: null }),
    )
    expect(countDifferences(rows)).toBe(0)
    expect(hasContradiction(rows)).toBe(false)
  })

  it('a shared telephone number under two different names', () => {
    const rows = compareIdentities(
      patient({ family_name: 'Oripov', given_name: 'Jahongir' }),
      patient({ family_name: 'Oripova', given_name: 'Nilufar', patronymic: null }),
    )
    // A household number. Two fields disagree, which is exactly what the
    // reader needs to see before merging a mother into her son.
    expect(countDifferences(rows)).toBe(2)
    expect(rowFor('phone', patient(), patient()).agreement).toBe('same')
  })

  it('shows the original spelling, not the normalized one', () => {
    const row = rowFor('family_name', patient({ family_name: 'ORIPOV' }), patient())
    expect(row.left).toBe('ORIPOV')
    expect(row.right).toBe('Oripov')
    expect(row.agreement).toBe('same')
  })
})
