/**
 * What the dashboard shows, decided apart from how it looks.
 *
 * These are the rules that can get a clinic into trouble, so they live in a
 * pure module with tests rather than inside a component:
 *
 *   a figure the person may not read must be **absent**, never rendered as
 *   zero — `0 UZS owed` shown to a receptionist who may not read the total
 *   is a lie in the shape of a fact;
 *
 *   an action appears only when the person may perform it *and* the screen it
 *   opens exists, because a button that leads nowhere is worse than no button:
 *   somebody plans their morning around it;
 *
 *   an exception is raised only from something the records prove — a till
 *   left open since an earlier day, duplicates awaiting a decision, finished
 *   work nobody has billed.
 *
 * Nothing here formats or translates. The component owns labels and money
 * formatting; this module owns *whether* a thing is shown, in what order,
 * with what emphasis, and where it leads. That split is what makes the rules
 * testable without a DOM.
 */
import type { TodayView } from './types'

export type Permission = (code: string) => boolean

// ------------------------------------------------------------------- tiles

export type TileKey =
  | 'takings'
  | 'debt'
  | 'advances'
  | 'drawer'
  | 'appointments'
  | 'patients'
  | 'treatments'
  | 'billable'

export interface TileSpec {
  key: TileKey
  /**
   * Overrides the tile's default heading. Only the till needs one: "Till
   * open" above a dash, when no session is open, is the kind of small lie
   * that makes somebody doubt the rest of the screen.
   */
  labelKey?: string
  /** A decimal string straight from the API; the component formats it. */
  amount?: string
  /** A plain count, or `null` for a tile that shows a dash. */
  count?: number | null
  /** `warning` for something that needs attention, not merely something big. */
  tone?: 'warning'
  /** Where the tile leads, when the person may open it. */
  to?: string
  /** Which hint to show, and the values to interpolate into it. */
  hint?: { key: string; params?: Record<string, string> }
}

/**
 * The tiles this person may see, in the order they matter to a clinic owner:
 * money first, then the day's work.
 *
 * A tile is built only where its figure is non-null, so the set of tiles *is*
 * the set of things this person is allowed to know.
 */
export function tilesFor(view: TodayView, can: Permission): TileSpec[] {
  const tiles: TileSpec[] = []

  if (view.payments) {
    tiles.push({
      key: 'takings',
      amount: view.payments.total,
      hint: {
        key: 'takingsSplit',
        params: { cash: view.payments.cash, card: view.payments.card },
      },
      to: can('sales.invoice.read') ? '/sales/payments' : undefined,
    })
  }
  if (view.debt_outstanding !== null) {
    tiles.push({
      key: 'debt',
      amount: view.debt_outstanding,
      hint: { key: 'debtHint' },
      to: can('reporting.financial.read') ? '/sales/ageing' : undefined,
    })
  }
  if (view.advances_available !== null) {
    tiles.push({
      key: 'advances',
      amount: view.advances_available,
      hint: { key: 'advancesHint' },
    })
  }
  if (view.drawer_readable) {
    tiles.push(
      view.drawer
        ? {
            key: 'drawer',
            labelKey: 'drawerOpen',
            amount: view.drawer.expected,
            // Warning only when the session is stale. An open till during the
            // working day is not a problem; one open since Tuesday is.
            tone: view.drawer.from_an_earlier_day ? 'warning' : undefined,
            hint: {
              key: 'drawerHint',
              params: { register: view.drawer.register_code },
            },
            to: '/cash',
          }
        : {
            key: 'drawer',
            labelKey: 'drawerClosed',
            count: null,
            hint: { key: 'drawerClosedHint' },
            to: '/cash',
          },
    )
  }
  if (view.appointments) {
    const a = view.appointments
    tiles.push({
      key: 'appointments',
      count: a.total,
      hint:
        a.total === 0
          ? { key: 'appointmentsNonePlanned' }
          : {
              key: 'appointmentsSplit',
              params: {
                expected: String(a.expected),
                arrived: String(a.arrived),
                completed: String(a.completed),
              },
            },
    })
  }
  if (view.patients_registered !== null) {
    tiles.push({
      key: 'patients',
      count: view.patients_registered,
      to: can('clinic.patient.read') ? '/clinic/patients' : undefined,
    })
  }
  if (view.treatments_in_progress !== null) {
    tiles.push({
      key: 'treatments',
      count: view.treatments_in_progress,
      hint: { key: 'treatmentsHint' },
    })
  }
  if (view.treatments_billable !== null) {
    tiles.push({
      key: 'billable',
      count: view.treatments_billable,
      // Finished work nobody has billed is money the clinic has earned and
      // not asked for, which is worth a colour.
      tone: view.treatments_billable > 0 ? 'warning' : undefined,
      hint: { key: 'billableHint' },
    })
  }
  return tiles
}

// -------------------------------------------------------------- exceptions

export interface ExceptionSpec {
  key: 'stale-drawer' | 'duplicates' | 'billable'
  tone: 'info' | 'warning'
  messageKey: string
  params?: Record<string, string>
  action?: { to: string; labelKey: string }
}

export function exceptionsFor(view: TodayView, can: Permission): ExceptionSpec[] {
  const items: ExceptionSpec[] = []

  if (view.drawer?.from_an_earlier_day) {
    items.push({
      key: 'stale-drawer',
      tone: 'warning',
      messageKey: 'drawerStale',
      params: {
        date: view.drawer.business_date,
        register: view.drawer.register_code,
      },
      action: can('cash.session.operate')
        ? { to: '/cash', labelKey: 'goToCash' }
        : undefined,
    })
  }
  if (view.duplicate_reviews_pending && view.duplicate_reviews_pending > 0) {
    items.push({
      key: 'duplicates',
      tone: 'info',
      messageKey: 'duplicatesWaiting',
      params: { n: String(view.duplicate_reviews_pending) },
      action: { to: '/clinic/duplicates', labelKey: 'goToDuplicates' },
    })
  }
  // Only to somebody who could act on it: telling a dentist that six bills
  // are unraised is noise, because they may not raise one.
  if (
    view.treatments_billable !== null &&
    view.treatments_billable > 0 &&
    can('sales.invoice.manage')
  ) {
    items.push({
      key: 'billable',
      tone: 'info',
      messageKey: 'billableWaiting',
      params: { n: String(view.treatments_billable) },
      action: { to: '/clinic/patients', labelKey: 'goToPatients' },
    })
  }
  return items
}

// ----------------------------------------------------------- quick actions

export interface ActionSpec {
  key: string
  to: string
  labelKey: string
}

/**
 * Actions this person may perform that lead to a screen that exists.
 *
 * There is deliberately no "record a treatment" action: the encounter and
 * per-tooth screens are not built, and offering one would be a decorative
 * button. When those screens land, this is the single place that changes.
 */
export function actionsFor(view: TodayView, can: Permission): ActionSpec[] {
  const actions: ActionSpec[] = []

  if (can('clinic.patient.manage')) {
    actions.push({
      key: 'register',
      to: '/clinic/patients',
      labelKey: 'actionRegisterPatient',
    })
  }
  if (can('clinic.patient.read')) {
    actions.push({
      key: 'find',
      to: '/clinic/patients',
      labelKey: 'actionFindPatient',
    })
  }
  if (can('sales.invoice.manage')) {
    actions.push({ key: 'invoice', to: '/sales/invoices', labelKey: 'actionInvoice' })
  }
  if (can('sales.payment.record')) {
    actions.push({ key: 'payment', to: '/sales/payments', labelKey: 'actionPayment' })
  }
  if (can('cash.session.operate')) {
    actions.push({
      key: 'drawer',
      to: '/cash',
      // The till is either open or it is not, and the useful action is the
      // other one.
      labelKey: view.drawer ? 'actionCloseDrawer' : 'actionOpenDrawer',
    })
  }
  if (can('reporting.financial.read')) {
    actions.push({ key: 'debts', to: '/sales/ageing', labelKey: 'actionDebts' })
  }
  return actions
}
