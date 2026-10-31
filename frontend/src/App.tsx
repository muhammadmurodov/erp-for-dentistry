import { Navigate, Route, Routes } from 'react-router-dom'
import { useAuth } from '@/features/auth/AuthProvider'
import { LoginPage } from '@/features/auth/LoginPage'
import { ChangePasswordGate } from '@/features/auth/ChangePasswordGate'
import { AppShell } from '@/components/AppShell'
import { DashboardPage } from '@/features/dashboard/DashboardPage'
import { SettingsPage } from '@/features/settings/SettingsPage'
import { AccountingPage } from '@/features/accounting/AccountingPage'
import { SalesPage } from '@/features/sales/SalesPage'
import { CashPage } from '@/features/cash/CashPage'
import { BankingPage } from '@/features/banking/BankingPage'
import { ModulePlaceholder } from '@/features/modules/ModulePlaceholder'
import { useI18n } from '@/i18n'
import { Alert, EmptyState, LoadingState, PageHeader } from '@/components/ui'

/** Modules whose screens are not built yet; each renders an honest placeholder. */
const PLACEHOLDER_ROUTES = [
  { path: 'clinic', key: 'clinic', titleKey: 'clinic' },
  { path: 'purchases', key: 'purchases', titleKey: 'purchases' },
  { path: 'inventory', key: 'inventory', titleKey: 'inventory' },
  { path: 'employees', key: 'employees', titleKey: 'employees' },
  { path: 'payroll', key: 'payroll', titleKey: 'payroll' },
  { path: 'reports', key: 'reports', titleKey: 'reports' },
  { path: 'documents', key: 'documents', titleKey: 'documents' },
] as const

export function App() {
  const { t } = useI18n()
  const { status, user } = useAuth()

  if (status === 'initialising') {
    return (
      <div className="flex min-h-full items-center justify-center">
        <LoadingState label={t.common.loading} />
      </div>
    )
  }

  if (status === 'anonymous') return <LoginPage />

  if (user?.must_change_password) return <ChangePasswordGate />

  // A signed-in user with no membership cannot act on any organization's
  // books. Say so rather than showing an empty shell.
  if (!user?.active_membership) {
    return (
      <div className="mx-auto max-w-lg px-4 py-16">
        <Alert tone="warning">{t.auth.noOrganization}</Alert>
      </div>
    )
  }

  return (
    <Routes>
      <Route element={<AppShell />} path="/">
        <Route index element={<DashboardPage />} />
        {PLACEHOLDER_ROUTES.map((route) => (
          <Route
            key={route.path}
            path={route.path}
            element={
              <ModulePlaceholder
                moduleKey={route.key}
                titleKey={route.titleKey}
              />
            }
          />
        ))}
        <Route path="sales/*" element={<SalesPage />} />
        <Route path="cash" element={<CashPage />} />
        <Route path="banking/*" element={<BankingPage />} />
        <Route path="accounting/*" element={<AccountingPage />} />
        <Route path="settings/*" element={<SettingsPage />} />
        <Route path="404" element={<NotFound />} />
        <Route path="*" element={<Navigate to="/404" replace />} />
      </Route>
    </Routes>
  )
}

function NotFound() {
  const { t } = useI18n()
  return (
    <>
      <PageHeader title={t.errors.pageNotFoundTitle} />
      <EmptyState
        title={t.errors.pageNotFoundTitle}
        body={t.errors.pageNotFoundBody}
      />
    </>
  )
}
