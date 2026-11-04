import { NavLink, Navigate, Route, Routes } from 'react-router-dom'
import clsx from 'clsx'
import { useI18n } from '@/i18n'
import { useAuth } from '@/features/auth/AuthProvider'
import { Alert, PageHeader } from '@/components/ui'
import { SuppliersPanel } from './SuppliersPanel'
import { OrdersPanel } from './OrdersPanel'
import { BillsPanel } from './BillsPanel'
import { PayablesPanel } from './PayablesPanel'

/**
 * Buying things: suppliers, orders, deliveries, invoices and what is owed.
 *
 * Four tabs because the brief's four states are four records, and they are
 * worked by different people at different times: the person who raises an
 * order is not the person who takes the delivery, and neither is the person
 * who registers the invoice. The tabs a caller sees follow their
 * permissions, so a screen is never offered that will refuse them.
 */
export function PurchasingPage() {
  const { t } = useI18n()
  const { can } = useAuth()

  const mayReadSuppliers = can('purchasing.supplier.read')
  const mayReadOrders = can('purchasing.order.read')

  if (!mayReadSuppliers && !mayReadOrders) {
    return (
      <>
        <PageHeader title={t.purchasing.title} />
        <Alert tone="warning">{t.purchasing.noAccess}</Alert>
      </>
    )
  }

  return (
    <>
      <PageHeader title={t.purchasing.title} />
      <Tabs />
      <Routes>
        <Route
          index
          element={<Navigate to={mayReadSuppliers ? 'suppliers' : 'orders'} replace />}
        />
        {mayReadSuppliers ? (
          <Route path="suppliers" element={<SuppliersPanel />} />
        ) : null}
        {mayReadOrders ? <Route path="orders" element={<OrdersPanel />} /> : null}
        {mayReadOrders ? <Route path="bills" element={<BillsPanel />} /> : null}
        {mayReadOrders ? <Route path="payables" element={<PayablesPanel />} /> : null}
        <Route
          path="*"
          element={<Navigate to={mayReadSuppliers ? 'suppliers' : 'orders'} replace />}
        />
      </Routes>
    </>
  )
}

function Tabs() {
  const { t } = useI18n()
  const { can } = useAuth()
  const tabs = [
    ...(can('purchasing.supplier.read')
      ? [{ to: 'suppliers', label: t.purchasing.suppliersTab }]
      : []),
    ...(can('purchasing.order.read')
      ? [
          { to: 'orders', label: t.purchasing.ordersTab },
          { to: 'bills', label: t.purchasing.billsTab },
          { to: 'payables', label: t.purchasing.payablesTab },
        ]
      : []),
  ]
  if (tabs.length <= 1) return null

  return (
    <nav aria-label={t.purchasing.title} className="mb-5">
      <ul className="flex flex-wrap gap-1 border-b border-[var(--border-subtle)]">
        {tabs.map((tab) => (
          <li key={tab.to}>
            <NavLink
              to={tab.to}
              className={({ isActive }) =>
                clsx(
                  '-mb-px inline-flex items-center gap-2 border-b-2 px-3 py-2 text-sm',
                  isActive
                    ? 'border-brand-600 font-medium text-brand-700'
                    : 'border-transparent text-[var(--text-secondary)] hover:text-[var(--text-primary)]',
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
