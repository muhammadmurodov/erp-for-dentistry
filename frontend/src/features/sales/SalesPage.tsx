import { NavLink, Navigate, Route, Routes } from 'react-router-dom'
import clsx from 'clsx'
import { useI18n } from '@/i18n'
import { useAuth } from '@/features/auth/AuthProvider'
import { useModules } from '@/lib/queries'
import { Alert, Badge, PageHeader } from '@/components/ui'
import { InvoicesPanel } from './InvoicesPanel'
import { PaymentsPanel } from './PaymentsPanel'
import { CustomersPanel, ServicesPanel } from './CataloguePanel'
import { AgeingPanel } from './AgeingPanel'

export function SalesPage() {
  const { t } = useI18n()
  const { can } = useAuth()
  const modules = useModules()
  const info = modules.data?.find((m) => m.key === 'sales')

  const tabs = [
    {
      to: 'invoices',
      label: t.sales.invoicesTab,
      visible: can('sales.invoice.read'),
    },
    {
      to: 'payments',
      label: t.sales.paymentsTab,
      visible: can('sales.invoice.read'),
    },
    {
      to: 'customers',
      label: t.sales.customersTab,
      visible: can('sales.invoice.read'),
    },
    {
      to: 'services',
      label: t.sales.servicesTab,
      visible: can('sales.service.read'),
    },
    {
      to: 'ageing',
      label: t.sales.ageingTab,
      visible: can('reporting.financial.read'),
    },
  ].filter((tab) => tab.visible)

  const first = tabs[0]?.to ?? 'invoices'

  return (
    <>
      <PageHeader
        title={t.sales.title}
        actions={
          info ? <Badge tone="warning">{t.moduleStatus.partial}</Badge> : undefined
        }
      />

      {info && info.missing_features.length > 0 ? (
        <div className="mb-5">
          <Alert tone="info">
            {t.moduleStatus.whatIsMissing}: {info.missing_features.join(' · ')}
          </Alert>
        </div>
      ) : null}

      <nav aria-label={t.sales.title} className="mb-5">
        <ul className="flex flex-wrap gap-1 border-b border-[var(--border-subtle)]">
          {tabs.map((tab) => (
            <li key={tab.to}>
              <NavLink
                to={tab.to}
                className={({ isActive }) =>
                  clsx(
                    '-mb-px inline-block border-b-2 px-3 py-2 text-sm',
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

      <Routes>
        <Route index element={<Navigate to={first} replace />} />
        {can('sales.invoice.read') ? (
          <>
            <Route path="invoices" element={<InvoicesPanel />} />
            <Route path="payments" element={<PaymentsPanel />} />
            <Route path="customers" element={<CustomersPanel />} />
          </>
        ) : null}
        {can('sales.service.read') ? (
          <Route path="services" element={<ServicesPanel />} />
        ) : null}
        {can('reporting.financial.read') ? (
          <Route path="ageing" element={<AgeingPanel />} />
        ) : null}
        <Route path="*" element={<Navigate to={first} replace />} />
      </Routes>
    </>
  )
}
