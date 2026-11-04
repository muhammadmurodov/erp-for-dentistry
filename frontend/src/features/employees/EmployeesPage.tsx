import { useState } from 'react'
import { NavLink, Navigate, Route, Routes } from 'react-router-dom'
import clsx from 'clsx'
import { useI18n } from '@/i18n'
import { useAuth } from '@/features/auth/AuthProvider'
import { Alert, PageHeader } from '@/components/ui'
import { PeoplePanel } from './PeoplePanel'
import { TimesheetPanel } from './TimesheetPanel'

/**
 * The employees module: who works here, and what each day was.
 *
 * Two tabs because the two jobs have different rhythms and different
 * permissions. Editing somebody's employment happens a few times a year and
 * needs `hr.employee.manage`; filling in the timesheet happens every week and
 * needs only `hr.attendance.record`. A clinic should be able to let one
 * person do the second without the first, and tabs are how that shows.
 */
export function EmployeesPage() {
  const { t } = useI18n()
  const { can } = useAuth()

  if (!can('hr.employee.read')) {
    return (
      <>
        <PageHeader title={t.employees.title} />
        <Alert tone="warning">{t.employees.noAccess}</Alert>
      </>
    )
  }

  return (
    <>
      <PageHeader title={t.employees.title} />
      <Tabs />
      <Routes>
        <Route index element={<Navigate to="people" replace />} />
        <Route path="people" element={<PeoplePanel />} />
        <Route path="timesheet" element={<TimesheetPanel />} />
        <Route path="*" element={<Navigate to="people" replace />} />
      </Routes>
    </>
  )
}

function Tabs() {
  const { t } = useI18n()
  const tabs = [
    { to: 'people', label: t.employees.peopleTab },
    { to: 'timesheet', label: t.employees.attendanceTab },
  ]
  return (
    <nav aria-label={t.employees.title} className="mb-5">
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

/** Shared by both panels: a month chooser bound to the clinic's own date. */
export function useMonthState(today: string): {
  year: number
  month: number
  setYear: (y: number) => void
  setMonth: (m: number) => void
} {
  const [year, setYear] = useState(Number(today.slice(0, 4)))
  const [month, setMonth] = useState(Number(today.slice(5, 7)))
  return { year, month, setYear, setMonth }
}
