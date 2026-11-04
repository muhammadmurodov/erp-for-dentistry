import { NavLink, Navigate, Route, Routes } from 'react-router-dom'
import clsx from 'clsx'
import { useI18n } from '@/i18n'
import { useAuth } from '@/features/auth/AuthProvider'
import { Alert, PageHeader } from '@/components/ui'
import { RunsPanel } from './RunsPanel'
import { RulesPanel } from './RulesPanel'
import { SalariesPanel } from './SalariesPanel'

/**
 * Paying the clinic's three people, by rules somebody approved.
 *
 * Runs first, because that is the monthly job. The rules and the salaries
 * behind it are maintained rarely — but a run that cannot proceed says which
 * rule is missing and links the person to it, so the ordering costs nothing
 * when something is wrong.
 *
 * No screen in this module contains a statutory rate. Every rate shown comes
 * from an approved rule version, and every payslip line names the version
 * that produced it. That is the point of the module: a figure somebody can
 * trace is worth more than a figure somebody typed.
 */
export function PayrollPage() {
  const { t } = useI18n()
  const { can } = useAuth()

  if (!can('payroll.run.read')) {
    return (
      <>
        <PageHeader title={t.payroll.title} />
        <Alert tone="warning">{t.payroll.noAccess}</Alert>
      </>
    )
  }

  return (
    <>
      <PageHeader title={t.payroll.title} description={t.payroll.subtitle} />
      <Tabs />
      <Routes>
        <Route index element={<Navigate to="runs" replace />} />
        <Route path="runs" element={<RunsPanel />} />
        <Route path="rules" element={<RulesPanel />} />
        <Route path="salaries" element={<SalariesPanel />} />
        <Route path="*" element={<Navigate to="runs" replace />} />
      </Routes>
    </>
  )
}

function Tabs() {
  const { t } = useI18n()
  const tabs = [
    { to: 'runs', label: t.payroll.runsTab },
    { to: 'rules', label: t.payroll.rulesTab },
    { to: 'salaries', label: t.payroll.salariesTab },
  ]

  return (
    <nav aria-label={t.payroll.title} className="mb-5">
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
