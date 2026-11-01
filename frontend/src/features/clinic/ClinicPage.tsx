import { useState } from 'react'
import { useI18n } from '@/i18n'
import { useAuth } from '@/features/auth/AuthProvider'
import { Alert, PageHeader } from '@/components/ui'
import { PatientsPanel } from './PatientsPanel'
import { PatientAccount } from './PatientAccount'

/**
 * The clinic screen: find a patient, then work on their account.
 *
 * One page with a selection rather than two routes, because that is how the
 * work goes: reception searches, opens somebody, takes the money, and goes
 * back to the list for the next person in the queue.
 */
export function ClinicPage() {
  const { t } = useI18n()
  const { can } = useAuth()
  const [patientId, setPatientId] = useState<string | null>(null)

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
      {patientId ? (
        <PatientAccount patientId={patientId} onBack={() => setPatientId(null)} />
      ) : (
        <PatientsPanel onOpen={setPatientId} />
      )}
    </>
  )
}
