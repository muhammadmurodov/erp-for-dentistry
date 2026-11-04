import { NavLink, Navigate, Route, Routes } from 'react-router-dom'
import clsx from 'clsx'
import { useI18n } from '@/i18n'
import { useAuth } from '@/features/auth/AuthProvider'
import { Alert, PageHeader } from '@/components/ui'
import { MaterialsPanel } from './MaterialsPanel'
import { StockPanel } from './StockPanel'
import { MovementsPanel } from './MovementsPanel'
import { CountsPanel } from './CountsPanel'

/**
 * The store cupboard: what the clinic has, what moved, and what was counted.
 *
 * Stock first rather than the catalogue, because the question somebody opens
 * this screen to answer is almost always "have we got any left" — the list
 * of materials is maintained once and then rarely touched.
 *
 * There is no tab for receiving. Stock arrives through the delivery recorded
 * in Purchases, which is the only door it has, so that every quantity on a
 * shelf can be traced back to a delivery note.
 */
export function InventoryPage() {
  const { t } = useI18n()
  const { can } = useAuth()

  if (!can('inventory.item.read')) {
    return (
      <>
        <PageHeader title={t.inventory.title} />
        <Alert tone="warning">{t.inventory.noAccess}</Alert>
      </>
    )
  }

  return (
    <>
      <PageHeader title={t.inventory.title} description={t.inventory.subtitle} />
      <Tabs />
      <Routes>
        <Route index element={<Navigate to="stock" replace />} />
        <Route path="stock" element={<StockPanel />} />
        <Route path="materials" element={<MaterialsPanel />} />
        <Route path="movements" element={<MovementsPanel />} />
        <Route path="counts" element={<CountsPanel />} />
        <Route path="*" element={<Navigate to="stock" replace />} />
      </Routes>
    </>
  )
}

function Tabs() {
  const { t } = useI18n()
  const tabs = [
    { to: 'stock', label: t.inventory.stockTab },
    { to: 'materials', label: t.inventory.materialsTab },
    { to: 'movements', label: t.inventory.movementsTab },
    { to: 'counts', label: t.inventory.countsTab },
  ]

  return (
    <nav aria-label={t.inventory.title} className="mb-5">
      <ul className="flex flex-wrap gap-1 border-b border-[var(--border-subtle)]">
        {tabs.map((tab) => (
          <li key={tab.to}>
            <NavLink
              to={tab.to}
              className={({ isActive }) =>
                clsx(
                  'inline-block border-b-2 px-3 py-2 text-sm font-medium transition-colors',
                  isActive
                    ? 'border-[var(--accent)] text-[var(--accent)]'
                    : 'border-transparent text-[var(--text-muted)] hover:text-[var(--text)]',
                )
              }
            >
              {tab.label}
            </NavLink>
          </li>
        ))}
      </ul>
    </nav>
  )
}
