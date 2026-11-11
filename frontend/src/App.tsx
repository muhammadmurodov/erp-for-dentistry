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
import { ClinicPage } from '@/features/clinic/ClinicPage'
import { BankingPage } from '@/features/banking/BankingPage'
import { EmployeesPage } from '@/features/employees/EmployeesPage'
import { PurchasingPage } from '@/features/purchasing/PurchasingPage'
import { InventoryPage } from '@/features/inventory/InventoryPage'
import { ReportsPage } from '@/features/reports/ReportsPage'
import { PayrollPage } from '@/features/payroll/PayrollPage'
import { DocumentsPage } from '@/features/documents/DocumentsPage'
import { useI18n } from '@/i18n'
import { Alert, EmptyState, LoadingState, PageHeader } from '@/components/ui'

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
        <Route path="documents" element={<DocumentsPage />} />
        <Route path="clinic/*" element={<ClinicPage />} />
        <Route path="sales/*" element={<SalesPage />} />
        <Route path="cash" element={<CashPage />} />
        <Route path="banking/*" element={<BankingPage />} />
        <Route path="employees/*" element={<EmployeesPage />} />
        <Route path="purchases/*" element={<PurchasingPage />} />
        <Route path="inventory/*" element={<InventoryPage />} />
        <Route path="reports/*" element={<ReportsPage />} />
        <Route path="payroll/*" element={<PayrollPage />} />
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
