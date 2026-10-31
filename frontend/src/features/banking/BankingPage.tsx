import { NavLink, Navigate, Route, Routes } from 'react-router-dom'
import clsx from 'clsx'
import { useI18n } from '@/i18n'
import { useAuth } from '@/features/auth/AuthProvider'
import { useModules } from '@/lib/queries'
import { Alert, Badge, PageHeader } from '@/components/ui'
import { ReconcilePanel } from './ReconcilePanel'
import { StatementsPanel } from './StatementsPanel'
import { AccountsPanel } from './AccountsPanel'

export function BankingPage() {
  const { t } = useI18n()
  const { can } = useAuth()
  const modules = useModules()
  const info = modules.data?.find((m) => m.key === 'banking')

  const mayRead = can('banking.account.read')

  if (!mayRead) {
    return (
      <>
        <PageHeader title={t.banking.title} />
        <Alert tone="warning">{t.moduleStatus.noAccess}</Alert>
      </>
    )
  }

  const tabs = [
    { to: 'reconcile', label: t.banking.reconcileTab },
    { to: 'statements', label: t.banking.statementsTab },
    { to: 'accounts', label: t.banking.accountsTab },
  ]

  return (
    <>
      <PageHeader
        title={t.banking.title}
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

      <nav aria-label={t.banking.title} className="mb-5">
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
        <Route index element={<Navigate to="reconcile" replace />} />
        <Route path="reconcile" element={<ReconcilePanel />} />
        <Route path="statements" element={<StatementsPanel />} />
        <Route path="accounts" element={<AccountsPanel />} />
        <Route path="*" element={<Navigate to="reconcile" replace />} />
      </Routes>
    </>
  )
}
