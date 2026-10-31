import { NavLink, Navigate, Route, Routes } from 'react-router-dom'
import clsx from 'clsx'
import { useI18n } from '@/i18n'
import { useAuth } from '@/features/auth/AuthProvider'
import { useModules } from '@/lib/queries'
import { Alert, Badge, PageHeader } from '@/components/ui'
import { ChartOfAccounts } from './ChartOfAccounts'
import { JournalList } from './JournalList'
import { PeriodsPanel } from './PeriodsPanel'
import { PolicyPanel } from './PolicyPanel'
import { TrialBalancePanel } from './TrialBalancePanel'

export function AccountingPage() {
  const { t } = useI18n()
  const { can } = useAuth()
  const modules = useModules()
  const info = modules.data?.find((m) => m.key === 'accounting')

  const tabs = [
    {
      to: 'journal',
      label: t.accounting.journalTab,
      visible: can('accounting.journal.read'),
    },
    {
      to: 'chart',
      label: t.accounting.chartTab,
      visible: can('accounting.account.read'),
    },
    {
      to: 'periods',
      label: t.accounting.periodsTab,
      visible: can('organization.period.read'),
    },
    {
      to: 'policy',
      label: t.accounting.policiesTab,
      visible: can('accounting.policy.read'),
    },
    {
      to: 'trial-balance',
      label: t.accounting.trialBalanceTab,
      visible: can('reporting.financial.read'),
    },
  ].filter((tab) => tab.visible)

  const first = tabs[0]?.to ?? 'journal'

  return (
    <>
      <PageHeader
        title={t.accounting.title}
        actions={
          info ? <Badge tone="warning">{t.moduleStatus.partial}</Badge> : undefined
        }
      />

      {/*
        The module is partly built, and the page says so with the specific
        list rather than leaving the gap to be discovered.
      */}
      {info && info.missing_features.length > 0 ? (
        <div className="mb-5">
          <Alert tone="info">
            {t.moduleStatus.whatIsMissing}: {info.missing_features.join(' · ')}
          </Alert>
        </div>
      ) : null}

      <nav aria-label={t.accounting.title} className="mb-5">
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
        {can('accounting.journal.read') ? (
          <Route path="journal" element={<JournalList />} />
        ) : null}
        {can('accounting.account.read') ? (
          <Route path="chart" element={<ChartOfAccounts />} />
        ) : null}
        {can('organization.period.read') ? (
          <Route path="periods" element={<PeriodsPanel />} />
        ) : null}
        {can('accounting.policy.read') ? (
          <Route path="policy" element={<PolicyPanel />} />
        ) : null}
        {can('reporting.financial.read') ? (
          <Route path="trial-balance" element={<TrialBalancePanel />} />
        ) : null}
        <Route path="*" element={<Navigate to={first} replace />} />
      </Routes>
    </>
  )
}
