import { describe, expect, it } from 'vitest'
import { actionsFor, exceptionsFor, tilesFor, type Permission } from './dashboard'
import type { TodayView } from './types'

/** Everything visible, as the owner sees it. Tests narrow from here. */
function view(overrides: Partial<TodayView> = {}): TodayView {
  return {
    business_date: '2026-11-01',
    currency: 'UZS',
    appointments: {
      total: 4,
      expected: 2,
      arrived: 1,
      completed: 1,
      cancelled: 0,
      no_show: 0,
    },
    patients_registered: 3,
    treatments_in_progress: 2,
    treatments_billable: 0,
    payments: {
      cash: '400000.00',
      card: '600000.00',
      transfer: '0.00',
      total: '1000000.00',
      count: 2,
    },
    debt_outstanding: '1250000.00',
    advances_available: '6000000.00',
    drawer: null,
    drawer_readable: true,
    duplicate_reviews_pending: 0,
    ...overrides,
  }
}

/** A person holding exactly these permissions and no others. */
function holding(...codes: string[]): Permission {
  return (code) => codes.includes(code)
}

const everything: Permission = () => true
const nothing: Permission = () => false

function keys(specs: { key: string }[]): string[] {
  return specs.map((s) => s.key)
}

describe('which tiles appear', () => {
  it('shows every figure to somebody allowed all of them', () => {
    expect(keys(tilesFor(view(), everything))).toEqual([
      'takings',
      'debt',
      'advances',
      'drawer',
      'appointments',
      'patients',
      'treatments',
      'billable',
    ])
  })

  it('puts the money first, because that is what the owner opens it for', () => {
    const order = keys(tilesFor(view(), everything))
    expect(order.indexOf('takings')).toBeLessThan(order.indexOf('appointments'))
    expect(order.indexOf('debt')).toBeLessThan(order.indexOf('patients'))
  })
})

describe('a withheld figure is absent, never zero', () => {
  it('omits the money tiles entirely when the API withheld them', () => {
    const reception = view({
      payments: null,
      debt_outstanding: null,
      advances_available: null,
      drawer_readable: false,
      treatments_in_progress: null,
      treatments_billable: null,
    })
    const shown = keys(tilesFor(reception, everything))
    expect(shown).not.toContain('takings')
    expect(shown).not.toContain('debt')
    expect(shown).not.toContain('advances')
    expect(shown).toEqual(['appointments', 'patients'])
  })

  it('never invents a zero for a figure that was not returned', () => {
    const tiles = tilesFor(view({ debt_outstanding: null }), everything)
    expect(tiles.find((t) => t.key === 'debt')).toBeUndefined()
  })

  it('does show a real zero, because nothing happened is a real answer', () => {
    const quiet = view({
      payments: { cash: '0.00', card: '0.00', transfer: '0.00', total: '0.00', count: 0 },
      debt_outstanding: '0.00',
    })
    const tiles = tilesFor(quiet, everything)
    expect(tiles.find((t) => t.key === 'takings')?.amount).toBe('0.00')
    expect(tiles.find((t) => t.key === 'debt')?.amount).toBe('0.00')
  })
})

describe('the till', () => {
  it('tells "no session open" apart from "not your business"', () => {
    const closed = tilesFor(view({ drawer: null, drawer_readable: true }), everything)
    expect(keys(closed)).toContain('drawer')

    const withheld = tilesFor(view({ drawer: null, drawer_readable: false }), everything)
    expect(keys(withheld)).not.toContain('drawer')
  })

  it('names itself for the state it is in', () => {
    // Regression: the tile showed "Till open" above a dash when no session
    // was open, which is the kind of small lie that makes somebody doubt the
    // rest of the screen.
    const closed = tilesFor(view({ drawer: null, drawer_readable: true }), everything)
    expect(closed.find((t) => t.key === 'drawer')?.labelKey).toBe('drawerClosed')

    const open = view({
      drawer: {
        session_id: 's',
        register_code: 'TILL-1',
        business_date: '2026-11-01',
        expected: '250000.00',
        from_an_earlier_day: false,
      },
    })
    expect(tilesFor(open, everything).find((t) => t.key === 'drawer')?.labelKey).toBe(
      'drawerOpen',
    )
  })

  it('shows a dash rather than a nought when nothing is open', () => {
    const closed = tilesFor(view({ drawer: null, drawer_readable: true }), everything)
    const tile = closed.find((t) => t.key === 'drawer')
    expect(tile?.amount).toBeUndefined()
    expect(tile?.count).toBeNull()
  })

  it('does not colour an ordinary open till', () => {
    const open = view({
      drawer: {
        session_id: 's',
        register_code: 'TILL-1',
        business_date: '2026-11-01',
        expected: '250000.00',
        from_an_earlier_day: false,
      },
    })
    expect(tilesFor(open, everything).find((t) => t.key === 'drawer')?.tone).toBeUndefined()
  })

  it('colours one left open since an earlier day', () => {
    const stale = view({
      drawer: {
        session_id: 's',
        register_code: 'TILL-1',
        business_date: '2026-10-29',
        expected: '250000.00',
        from_an_earlier_day: true,
      },
    })
    expect(tilesFor(stale, everything).find((t) => t.key === 'drawer')?.tone).toBe('warning')
  })
})

describe('finished work nobody has billed', () => {
  it('is not coloured when there is none', () => {
    expect(
      tilesFor(view({ treatments_billable: 0 }), everything).find((t) => t.key === 'billable')
        ?.tone,
    ).toBeUndefined()
  })

  it('is coloured when there is, because it is money not asked for', () => {
    expect(
      tilesFor(view({ treatments_billable: 3 }), everything).find((t) => t.key === 'billable')
        ?.tone,
    ).toBe('warning')
  })
})

describe('tiles link only where the person may follow', () => {
  it('does not link the debt tile without the reporting permission', () => {
    const tiles = tilesFor(view(), holding('sales.invoice.read'))
    expect(tiles.find((t) => t.key === 'debt')?.to).toBeUndefined()
    expect(tiles.find((t) => t.key === 'takings')?.to).toBe('/sales/payments')
  })

  it('links the debt tile for somebody who may read the report', () => {
    const tiles = tilesFor(view(), holding('reporting.financial.read'))
    expect(tiles.find((t) => t.key === 'debt')?.to).toBe('/sales/ageing')
  })

  it('still shows the figure it cannot link to', () => {
    // The API already decided they may see the number; the link is only
    // about whether the screen behind it would admit them.
    const tiles = tilesFor(view(), nothing)
    expect(tiles.find((t) => t.key === 'debt')?.amount).toBe('1250000.00')
  })
})

describe('quick actions', () => {
  it('offers nothing to somebody with no permissions', () => {
    expect(actionsFor(view(), nothing)).toEqual([])
  })

  it('offers registration only to somebody who may register', () => {
    expect(keys(actionsFor(view(), holding('clinic.patient.read')))).toEqual(['find'])
    expect(keys(actionsFor(view(), holding('clinic.patient.manage')))).toEqual(['register'])
  })

  it('never offers a clinical workflow that has no screen', () => {
    // The encounter and per-tooth screens are not built. Until they are,
    // there must be no button pretending otherwise.
    const labels = actionsFor(view(), everything).map((a) => a.labelKey)
    expect(labels).not.toContain('actionStartTreatment')
    expect(labels).not.toContain('actionRecordTreatment')
  })

  it('offers to open the till when none is open', () => {
    const [action] = actionsFor(view({ drawer: null }), holding('cash.session.operate'))
    expect(action?.labelKey).toBe('actionOpenDrawer')
  })

  it('offers to close it when one is', () => {
    const open = view({
      drawer: {
        session_id: 's',
        register_code: 'TILL-1',
        business_date: '2026-11-01',
        expected: '0.00',
        from_an_earlier_day: false,
      },
    })
    const [action] = actionsFor(open, holding('cash.session.operate'))
    expect(action?.labelKey).toBe('actionCloseDrawer')
  })
})

describe('exceptions', () => {
  it('raises nothing on a quiet day', () => {
    expect(exceptionsFor(view(), everything)).toEqual([])
  })

  it('raises a till left open from an earlier day, as a warning', () => {
    const stale = view({
      drawer: {
        session_id: 's',
        register_code: 'TILL-1',
        business_date: '2026-10-29',
        expected: '250000.00',
        from_an_earlier_day: true,
      },
    })
    const [item] = exceptionsFor(stale, everything)
    expect(item?.key).toBe('stale-drawer')
    expect(item?.tone).toBe('warning')
    expect(item?.params).toMatchObject({ register: 'TILL-1', date: '2026-10-29' })
  })

  it('offers the till link only to somebody who may work a session', () => {
    const stale = view({
      drawer: {
        session_id: 's',
        register_code: 'TILL-1',
        business_date: '2026-10-29',
        expected: '0.00',
        from_an_earlier_day: true,
      },
    })
    expect(exceptionsFor(stale, holding('cash.register.read'))[0]?.action).toBeUndefined()
    expect(exceptionsFor(stale, holding('cash.session.operate'))[0]?.action?.to).toBe('/cash')
  })

  it('raises duplicates waiting for a decision', () => {
    const [item] = exceptionsFor(view({ duplicate_reviews_pending: 7 }), everything)
    expect(item?.key).toBe('duplicates')
    expect(item?.params).toEqual({ n: '7' })
  })

  it('says nothing about duplicates when the count was withheld', () => {
    expect(exceptionsFor(view({ duplicate_reviews_pending: null }), everything)).toEqual([])
  })

  it('mentions unbilled work only to somebody who could raise the bill', () => {
    const unbilled = view({ treatments_billable: 4 })
    expect(keys(exceptionsFor(unbilled, holding('clinic.clinical.read')))).toEqual([])
    expect(keys(exceptionsFor(unbilled, holding('sales.invoice.manage')))).toEqual(['billable'])
  })

  it('puts the till before the paperwork', () => {
    const busy = view({
      duplicate_reviews_pending: 2,
      treatments_billable: 1,
      drawer: {
        session_id: 's',
        register_code: 'TILL-1',
        business_date: '2026-10-29',
        expected: '0.00',
        from_an_earlier_day: true,
      },
    })
    expect(keys(exceptionsFor(busy, everything))).toEqual([
      'stale-drawer',
      'duplicates',
      'billable',
    ])
  })
})
