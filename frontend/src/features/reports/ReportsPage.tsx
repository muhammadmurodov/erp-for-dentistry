import { NavLink, Navigate, Route, Routes } from 'react-router-dom'
import clsx from 'clsx'
import { useI18n } from '@/i18n'
import { useAuth } from '@/features/auth/AuthProvider'
import { Alert, Card, CardHeader, PageHeader } from '@/components/ui'
import { useReportCatalogue } from '@/lib/queries'
import { blockedStatutoryReports } from '@/lib/reports'
import { ProfitAndLossPanel } from './ProfitAndLossPanel'
import { BalanceSheetPanel } from './BalanceSheetPanel'
import { ReceivablesPanel } from './ReceivablesPanel'
import { PayablesPanel } from './PayablesPanel'
import { CashFlowPanel } from './CashFlowPanel'

/**
 * Management reports, derived from posted entries every time they are opened.
 *
 * **Which reports exist is the backend's answer, not this screen's.** The
 * tabs are rendered from `GET /reporting/catalogue`, and the statutory forms
 * that are still blocked are listed at the foot with the question that
 * blocks each one. That is the correction to what this page used to be: a
 * "module not developed" placeholder standing in front of reports that
 * worked, and silence about the ones that cannot exist yet.
 */
export function ReportsPage() {
  const { t } = useI18n()
  const { can } = useAuth()
  const mayRead = can('reporting.financial.read')
  const catalogue = useReportCatalogue(mayRead)

  if (!mayRead) {
    return (
      <>
        <PageHeader title={t.reports.title} />
        <Alert tone="warning">{t.reports.noAccess}</Alert>
      </>
    )
  }

  return (
    <>
      <div className="print:hidden">
        <PageHeader title={t.reports.title} description={t.reports.subtitle} />
        <Tabs />
      </div>
      <Routes>
        <Route index element={<Navigate to="profit-and-loss" replace />} />
        <Route path="profit-and-loss" element={<ProfitAndLossPanel />} />
        <Route path="balance-sheet" element={<BalanceSheetPanel />} />
        <Route path="receivables" element={<ReceivablesPanel />} />
        <Route path="payables" element={<PayablesPanel />} />
        <Route path="cash-flow" element={<CashFlowPanel />} />
        <Route path="*" element={<Navigate to="profit-and-loss" replace />} />
      </Routes>
      <div className="print:hidden">
        <BlockedReports catalogue={catalogue.data} />
      </div>
    </>
  )
}

function Tabs() {
  const { t } = useI18n()
  const tabs = [
    { to: 'profit-and-loss', label: t.reports.profitAndLossTab },
    { to: 'balance-sheet', label: t.reports.balanceSheetTab },
    { to: 'receivables', label: t.reports.receivablesTab },
    { to: 'payables', label: t.reports.payablesTab },
    { to: 'cash-flow', label: t.reports.cashFlowTab },
  ]
  return (
    <nav aria-label={t.reports.title} className="mb-5">
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

/**
 * What cannot be produced yet, and why — straight from the backend.
 *
 * Stated rather than hidden: a clinic that needs a filing form should learn
 * here that it waits on an accountant's answer, not discover it on a
 * deadline.
 */
function BlockedReports({ catalogue }: { catalogue: ReturnType<typeof useReportCatalogue>['data'] }) {
  const { t } = useI18n()
  const blocked = blockedStatutoryReports(catalogue)
  if (blocked.length === 0) return null

  return (
    <Card className="mt-6">
      <CardHeader title={t.reports.blockedTitle} />
      <div className="space-y-2 px-4 pb-4 text-sm">
        <p className="text-[var(--text-muted)]">{t.reports.blockedBody}</p>
        <ul className="space-y-1">
          {blocked.map((report) => (
            <li key={report.key}>
              <strong>
                {t.reports.reportNames[report.key as keyof typeof t.reports.reportNames] ??
                  report.key}
              </strong>
              {report.blocked_by ? (
                <span className="text-[var(--text-muted)]">
                  {' '}
                  — {t.reports.waitingOn} {report.blocked_by}
                </span>
              ) : null}
            </li>
          ))}
        </ul>
      </div>
    </Card>
  )
}
