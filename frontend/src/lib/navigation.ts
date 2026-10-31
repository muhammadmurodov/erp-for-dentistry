import type { Dictionary } from '@/i18n/uz'

/**
 * The primary navigation, in the order the interface requirements specify.
 *
 * `key` matches the backend module registry, which is what decides whether an
 * entry opens a working screen or a labelled placeholder. The list of entries
 * lives here; their *state* comes from the server.
 */
export interface NavEntry {
  key: string
  path: string
  label: (t: Dictionary) => string
  icon: string
}

export const NAV_ENTRIES: NavEntry[] = [
  { key: 'dashboard', path: '/', label: (t) => t.nav.dashboard, icon: 'grid' },
  { key: 'clinic', path: '/clinic', label: (t) => t.nav.clinic, icon: 'tooth' },
  { key: 'sales', path: '/sales', label: (t) => t.nav.sales, icon: 'receipt' },
  { key: 'cash', path: '/cash', label: (t) => t.nav.cash, icon: 'cash' },
  { key: 'banking', path: '/banking', label: (t) => t.nav.banking, icon: 'bank' },
  {
    key: 'purchases',
    path: '/purchases',
    label: (t) => t.nav.purchases,
    icon: 'cart',
  },
  {
    key: 'inventory',
    path: '/inventory',
    label: (t) => t.nav.inventory,
    icon: 'box',
  },
  {
    key: 'employees',
    path: '/employees',
    label: (t) => t.nav.employees,
    icon: 'people',
  },
  { key: 'payroll', path: '/payroll', label: (t) => t.nav.payroll, icon: 'wallet' },
  {
    key: 'accounting',
    path: '/accounting',
    label: (t) => t.nav.accounting,
    icon: 'ledger',
  },
  { key: 'reports', path: '/reports', label: (t) => t.nav.reports, icon: 'chart' },
  {
    key: 'documents',
    path: '/documents',
    label: (t) => t.nav.documents,
    icon: 'folder',
  },
  { key: 'settings', path: '/settings', label: (t) => t.nav.settings, icon: 'gear' },
]
