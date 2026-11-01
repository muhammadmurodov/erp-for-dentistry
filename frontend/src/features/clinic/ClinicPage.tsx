import { useState } from 'react'
import { NavLink, Navigate, Route, Routes } from 'react-router-dom'
import clsx from 'clsx'
import { useI18n } from '@/i18n'
import { useAuth } from '@/features/auth/AuthProvider'
import { useDuplicateReviews } from '@/lib/queries'
import { Alert, Badge, PageHeader } from '@/components/ui'
import { PatientsPanel } from './PatientsPanel'
import { PatientAccount } from './PatientAccount'
import { DuplicateReviewQueue } from './DuplicateReviewQueue'

/**
 * The clinic screen: find a patient and work on their account, or settle a
 * suspected duplicate.
 *
 * Two tabs, not two pages buried in a menu, because in a clinic run by two or
 * three people the same person does both: registers the patient at the desk,
 * then clears the duplicate queue when it is quiet. The badge on the
 * duplicates tab is the only reason anybody would think to look there.
 */
export function ClinicPage() {
  const { t } = useI18n()
  const { can } = useAuth()

  if (!can('clinic.patient.read')) {
    return (
      <>
        <PageHeader title={t.clinic.title} />
        <Alert tone="warning">{t.moduleStatus.noAccess}</Alert>
      </>
    )
  }

  return (
    <>
      <PageHeader title={t.clinic.title} />
      <ClinicTabs />
      <Routes>
        <Route index element={<Navigate to="patients" replace />} />
        <Route path="patients" element={<PatientsSection />} />
        <Route path="duplicates" element={<DuplicateReviewQueue />} />
        <Route path="*" element={<Navigate to="patients" replace />} />
      </Routes>
    </>
  )
}

function ClinicTabs() {
  const { t } = useI18n()
  const { can } = useAuth()
  // Only somebody who may settle a duplicate is shown the queue, and only
  // they trigger the request that counts it.
  const mayDecide = can('clinic.patient.manage')
  const reviews = useDuplicateReviews(mayDecide)
  const waiting = reviews.data?.length ?? 0

  const tabs = [
    { to: 'patients', label: t.clinic.patientsTabLabel, count: 0 },
    ...(mayDecide
      ? [{ to: 'duplicates', label: t.clinic.reviewQueueTab, count: waiting }]
      : []),
  ]

  if (tabs.length === 1) return null

  return (
    <nav aria-label={t.clinic.title} className="mb-5">
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
              {tab.count > 0 ? (
                <Badge tone="warning">{String(tab.count)}</Badge>
              ) : null}
            </NavLink>
          </li>
        ))}
      </ul>
    </nav>
  )
}

/**
 * Search, then work on one patient. A selection rather than a route, because
 * that is how the work goes: reception searches, opens somebody, takes the
 * money, and comes back to the list for the next person in the queue.
 */
function PatientsSection() {
  const [patientId, setPatientId] = useState<string | null>(null)
  return patientId ? (
    <PatientAccount patientId={patientId} onBack={() => setPatientId(null)} />
  ) : (
    <PatientsPanel onOpen={setPatientId} />
  )
}
