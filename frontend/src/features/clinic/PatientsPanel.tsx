import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useI18n } from '@/i18n'
import { useAuth } from '@/features/auth/AuthProvider'
import { api } from '@/lib/api'
import { usePatients } from '@/lib/queries'
import { describeError, errorRequestId } from '@/lib/errorMessage'
import {
  Alert,
  Button,
  Card,
  CardHeader,
  Dialog,
  EmptyState,
  ErrorState,
  Field,
  Input,
  LoadingState,
  Table,
  Td,
  Th,
} from '@/components/ui'
import type { PatientRegistered } from '@/lib/types'

/**
 * Finding and registering patients.
 *
 * Registration is never refused because somebody looks like a duplicate. The
 * clinic's paper register has no patient number and identifies people by name
 * and birth year alone, so duplicates are expected; the candidates are
 * reported, a review is queued, and the receptionist with a patient in front
 * of them can finish.
 */
export function PatientsPanel({ onOpen }: { onOpen: (id: string) => void }) {
  const { t, formatDate } = useI18n()
  const { can } = useAuth()
  const queryClient = useQueryClient()

  const mayManage = can('clinic.patient.manage')
  const [query, setQuery] = useState('')
  const [createOpen, setCreateOpen] = useState(false)
  const patients = usePatients(true, query.trim() ? { query: query.trim() } : {})

  return (
    <>
      <Card>
        <CardHeader
          title={t.clinic.patientsTab}
          actions={
            <div className="flex flex-wrap items-center gap-2">
              <Input
                className="w-60"
                placeholder={t.clinic.searchPlaceholder}
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                aria-label={t.common.search}
              />
              {mayManage ? (
                <Button variant="primary" size="sm" onClick={() => setCreateOpen(true)}>
                  {t.clinic.newPatient}
                </Button>
              ) : null}
            </div>
          }
        />

        {patients.isLoading ? (
          <LoadingState label={t.common.loading} />
        ) : patients.isError ? (
          <ErrorState
            title={t.errors.loadFailedTitle}
            message={describeError(patients.error, t)}
            requestId={errorRequestId(patients.error)}
            requestIdLabel={t.common.requestId}
            onRetry={() => void patients.refetch()}
            retryLabel={t.common.retry}
          />
        ) : (patients.data ?? []).length === 0 ? (
          <EmptyState title={t.clinic.emptyPatients} />
        ) : (
          <Table>
            <thead>
              <tr>
                <Th>{t.clinic.code}</Th>
                <Th>{t.clinic.familyName}</Th>
                <Th>{t.clinic.birthYear}</Th>
                <Th>{t.clinic.phone}</Th>
                <Th>{t.clinic.registeredOn}</Th>
                <Th>{t.common.actions}</Th>
              </tr>
            </thead>
            <tbody>
              {(patients.data ?? []).map((patient) => (
                <tr key={patient.id}>
                  <Td>
                    <span className="font-mono text-xs">{patient.code}</span>
                  </Td>
                  <Td>{patient.display_name}</Td>
                  <Td>{patient.birth_year ?? '—'}</Td>
                  <Td>{patient.phone ?? '—'}</Td>
                  <Td>{formatDate(patient.registered_on)}</Td>
                  <Td>
                    <Button size="sm" variant="ghost" onClick={() => onOpen(patient.id)}>
                      {t.common.edit}
                    </Button>
                  </Td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </Card>

      <RegisterDialog
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onRegistered={(id) => {
          void queryClient.invalidateQueries({ queryKey: ['clinic'] })
          setCreateOpen(false)
          onOpen(id)
        }}
      />
    </>
  )
}

function RegisterDialog({
  open,
  onClose,
  onRegistered,
}: {
  open: boolean
  onClose: () => void
  onRegistered: (patientId: string) => void
}) {
  const { t } = useI18n()
  const [form, setForm] = useState({
    family_name: '',
    given_name: '',
    patronymic: '',
    birth_year: '',
    phone: '',
    address: '',
  })
  const [candidates, setCandidates] = useState<PatientRegistered | null>(null)
  const [error, setError] = useState<string | null>(null)

  const register = useMutation({
    mutationFn: (allowDuplicate: boolean) =>
      api.post<PatientRegistered>('/clinic/patients', {
        family_name: form.family_name.trim(),
        given_name: form.given_name.trim(),
        patronymic: form.patronymic.trim() || null,
        birth_year: form.birth_year.trim() ? Number(form.birth_year.trim()) : null,
        phone: form.phone.trim() || null,
        address: form.address.trim() || null,
        allow_duplicate: allowDuplicate,
      }),
    onSuccess: (result) => {
      setError(null)
      // Registered either way. The candidates are shown so somebody decides,
      // not to undo what has just been recorded.
      if (result.duplicate_candidates.length > 0) {
        setCandidates(result)
        return
      }
      onRegistered(result.patient.id)
    },
    onError: (cause) => setError(describeError(cause, t)),
  })

  const ready = form.family_name.trim() !== '' && form.given_name.trim() !== ''

  return (
    <Dialog
      open={open}
      onClose={() => {
        setCandidates(null)
        setError(null)
        onClose()
      }}
      title={t.clinic.newPatient}
      closeLabel={t.common.close}
      footer={
        candidates ? (
          <Button variant="primary" onClick={() => onRegistered(candidates.patient.id)}>
            {t.common.confirm}
          </Button>
        ) : (
          <Button
            variant="primary"
            disabled={!ready}
            loading={register.isPending}
            onClick={() => register.mutate(false)}
          >
            {t.common.create}
          </Button>
        )
      }
    >
      {error ? (
        <div className="mb-4">
          <Alert tone="danger">{error}</Alert>
        </div>
      ) : null}

      {candidates ? (
        <div className="flex flex-col gap-3">
          <Alert tone="warning">{t.clinic.duplicateTitle}</Alert>
          <p className="text-sm text-[var(--text-secondary)]">{t.clinic.duplicateHint}</p>
          <ul className="flex flex-col gap-2">
            {candidates.duplicate_candidates.map((candidate) => (
              <li
                key={candidate.patient_id}
                className="rounded-lg border border-[var(--border-subtle)] px-3 py-2 text-sm"
              >
                <span className="font-medium">{candidate.display_name}</span>
                <span className="ml-2 font-mono text-xs text-[var(--text-muted)]">
                  {candidate.code}
                </span>
                <p className="text-xs text-[var(--text-secondary)]">{candidate.reason}</p>
              </li>
            ))}
          </ul>
        </div>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label={t.clinic.familyName} htmlFor="patient-family" required>
            <Input
              id="patient-family"
              value={form.family_name}
              onChange={(event) =>
                setForm((prev) => ({ ...prev, family_name: event.target.value }))
              }
            />
          </Field>
          <Field label={t.clinic.givenName} htmlFor="patient-given" required>
            <Input
              id="patient-given"
              value={form.given_name}
              onChange={(event) =>
                setForm((prev) => ({ ...prev, given_name: event.target.value }))
              }
            />
          </Field>
          <Field label={t.clinic.patronymic} htmlFor="patient-patronymic">
            <Input
              id="patient-patronymic"
              value={form.patronymic}
              onChange={(event) =>
                setForm((prev) => ({ ...prev, patronymic: event.target.value }))
              }
            />
          </Field>
          {/*
            A year, not a date, and optional — the clinic's own register
            leaves it blank on a good number of rows, and a form that insisted
            would be filled in with something invented.
          */}
          <Field label={t.clinic.birthYear} htmlFor="patient-birth-year">
            <Input
              id="patient-birth-year"
              inputMode="numeric"
              value={form.birth_year}
              onChange={(event) =>
                setForm((prev) => ({ ...prev, birth_year: event.target.value }))
              }
            />
          </Field>
          <Field label={t.clinic.phone} htmlFor="patient-phone">
            <Input
              id="patient-phone"
              value={form.phone}
              onChange={(event) => setForm((prev) => ({ ...prev, phone: event.target.value }))}
            />
          </Field>
          <Field label={t.clinic.address} htmlFor="patient-address">
            <Input
              id="patient-address"
              value={form.address}
              onChange={(event) =>
                setForm((prev) => ({ ...prev, address: event.target.value }))
              }
            />
          </Field>
        </div>
      )}
    </Dialog>
  )
}
