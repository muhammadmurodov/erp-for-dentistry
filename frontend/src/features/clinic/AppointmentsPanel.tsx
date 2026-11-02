import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useI18n } from '@/i18n'
import { useAuth } from '@/features/auth/AuthProvider'
import { api } from '@/lib/api'
import { useAppointments, useClinicDentists, usePatients } from '@/lib/queries'
import { describeError, errorRequestId } from '@/lib/errorMessage'
import {
  actionsFor,
  clashWith,
  formatClinicTime,
  isClosed,
  toApiTime,
  type Slot,
} from '@/lib/appointments'
import type { Appointment, AppointmentStatusValue } from '@/lib/types'
import {
  Alert,
  Badge,
  Button,
  Card,
  CardHeader,
  Dialog,
  EmptyState,
  ErrorState,
  Field,
  Input,
  LoadingState,
  Select,
  Table,
  Td,
  Th,
} from '@/components/ui'

/**
 * The day, as reception works it.
 *
 * One date at a time, because that is the question somebody at the desk has:
 * who is coming in today, who has arrived, who is still expected. A week view
 * is a planning tool and this clinic plans by telephone.
 *
 * What the screen is careful about:
 *
 * **The times are the clinic's.** They arrive as a local date and a local
 * time and are shown exactly as they came. No `Date` is constructed from
 * them, because that would read them as instants in the browser's timezone
 * and move every appointment by five hours for anybody abroad — including a
 * server rendering a report.
 *
 * **It warns about a double booking before the submit.** The server refuses
 * an overlap and is the authority, but a refusal after the dialog closes is
 * no use to somebody with a patient on the telephone.
 *
 * **It offers only what can happen.** A cancelled booking cannot be
 * confirmed and a finished one cannot become a no-show, so those buttons are
 * absent rather than present and failing.
 */
export function AppointmentsPanel() {
  const { t } = useI18n()
  const { can } = useAuth()
  const queryClient = useQueryClient()

  const mayRead = can('clinic.appointment.read')
  const mayManage = can('clinic.appointment.manage')

  const [day, setDay] = useState(() => todayInClinic())
  const [booking, setBooking] = useState(false)
  const [moving, setMoving] = useState<Appointment | null>(null)
  const [cancelling, setCancelling] = useState<Appointment | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const appointments = useAppointments(mayRead, { on: day })
  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ['clinic'] })
  }

  const setStatus = useMutation({
    mutationFn: (input: {
      id: string
      status: AppointmentStatusValue
      reason?: string
    }) =>
      api.post<Appointment>(`/clinic/appointments/${input.id}/status`, {
        status: input.status,
        reason: input.reason ?? null,
      }),
    onSuccess: (_row, input) => {
      setError(null)
      setNotice(t.appointments.statusChanged[input.status])
      invalidate()
    },
    onError: (caught) => {
      setNotice(null)
      setError(describeError(caught, t))
    },
  })

  if (!mayRead) return <Alert tone="warning">{t.moduleStatus.noAccess}</Alert>

  const rows = appointments.data ?? []

  return (
    <>
      <Card>
        <CardHeader
          title={t.appointments.title}
          description={t.appointments.hint}
          actions={
            mayManage ? (
              <Button
                variant="primary"
                size="sm"
                onClick={() => {
                  setError(null)
                  setBooking(true)
                }}
              >
                {t.appointments.book}
              </Button>
            ) : undefined
          }
        />

        <div className="mb-4 flex flex-wrap items-end gap-3">
          <Field label={t.appointments.day} htmlFor="appointments-day">
            <Input
              id="appointments-day"
              type="date"
              value={day}
              onChange={(event) => setDay(event.target.value)}
            />
          </Field>
          <Button size="sm" onClick={() => setDay(todayInClinic())}>
            {t.appointments.today}
          </Button>
          <Button size="sm" onClick={() => setDay(shiftDay(day, -1))}>
            {t.appointments.previousDay}
          </Button>
          <Button size="sm" onClick={() => setDay(shiftDay(day, 1))}>
            {t.appointments.nextDay}
          </Button>
        </div>

        {notice ? (
          <div className="mb-4">
            <Alert tone="success">{notice}</Alert>
          </div>
        ) : null}
        {error ? (
          <div className="mb-4">
            <Alert tone="danger">{error}</Alert>
          </div>
        ) : null}

        {appointments.isLoading ? <LoadingState label={t.common.loading} /> : null}
        {appointments.isError ? (
          <ErrorState
            title={t.errors.loadFailedTitle}
            message={describeError(appointments.error, t)}
            requestId={errorRequestId(appointments.error)}
            requestIdLabel={t.common.requestId}
            onRetry={() => void appointments.refetch()}
            retryLabel={t.common.retry}
          />
        ) : null}

        {!appointments.isLoading && rows.length === 0 ? (
          <EmptyState
            title={t.appointments.emptyDay}
            body={mayManage ? t.appointments.emptyDayHint : undefined}
          />
        ) : null}

        {rows.length > 0 ? (
          <Table>
            <thead>
              <tr>
                <Th>{t.appointments.time}</Th>
                <Th>{t.appointments.patient}</Th>
                <Th>{t.appointments.dentist}</Th>
                <Th>{t.appointments.reason}</Th>
                <Th>{t.appointments.status}</Th>
                <Th>{t.common.actions}</Th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => {
                const actions = actionsFor(row.status)
                return (
                  <tr key={row.id} id={`appointment-${row.patient_code}`}>
                    <Td>
                      <span className="tabular">
                        {formatClinicTime(row.scheduled_at) || t.appointments.noTime}
                      </span>
                      {row.duration_minutes !== null ? (
                        <span className="ml-2 text-xs text-[var(--text-secondary)]">
                          {t.appointments.minutes.replace(
                            '{n}',
                            String(row.duration_minutes),
                          )}
                        </span>
                      ) : null}
                    </Td>
                    <Td>
                      <div>{row.patient_name}</div>
                      <div className="text-xs text-[var(--text-secondary)]">
                        <span className="font-mono">{row.patient_code}</span>
                        {row.patient_phone ? ` · ${row.patient_phone}` : ''}
                      </div>
                    </Td>
                    <Td>{row.dentist_name ?? '—'}</Td>
                    <Td>{row.reason ?? row.cancelled_reason ?? '—'}</Td>
                    <Td>
                      <Badge tone={toneFor(row.status)}>
                        {t.appointments.statusLabel[row.status]}
                      </Badge>
                    </Td>
                    <Td>
                      {mayManage && !isClosed(row.status) ? (
                        <div className="flex flex-wrap gap-1">
                          {actions.confirm ? (
                            <Button
                              size="sm"
                              variant="ghost"
                              onClick={() =>
                                setStatus.mutate({ id: row.id, status: 'CONFIRMED' })
                              }
                            >
                              {t.appointments.confirm}
                            </Button>
                          ) : null}
                          {actions.arrive ? (
                            <Button
                              size="sm"
                              variant="ghost"
                              onClick={() =>
                                setStatus.mutate({ id: row.id, status: 'ARRIVED' })
                              }
                            >
                              {t.appointments.arrived}
                            </Button>
                          ) : null}
                          {actions.complete ? (
                            <Button
                              size="sm"
                              variant="ghost"
                              onClick={() =>
                                setStatus.mutate({ id: row.id, status: 'COMPLETED' })
                              }
                            >
                              {t.appointments.complete}
                            </Button>
                          ) : null}
                          {actions.reschedule ? (
                            <Button
                              size="sm"
                              variant="ghost"
                              onClick={() => {
                                setError(null)
                                setMoving(row)
                              }}
                            >
                              {t.appointments.reschedule}
                            </Button>
                          ) : null}
                          {actions.noShow ? (
                            <Button
                              size="sm"
                              variant="ghost"
                              onClick={() =>
                                setStatus.mutate({ id: row.id, status: 'NO_SHOW' })
                              }
                            >
                              {t.appointments.noShow}
                            </Button>
                          ) : null}
                          {actions.cancel ? (
                            <Button
                              size="sm"
                              variant="ghost"
                              onClick={() => {
                                setError(null)
                                setCancelling(row)
                              }}
                            >
                              {t.common.cancel}
                            </Button>
                          ) : null}
                        </div>
                      ) : null}
                    </Td>
                  </tr>
                )
              })}
            </tbody>
          </Table>
        ) : null}
      </Card>

      <BookingDialog
        open={booking || moving !== null}
        day={day}
        existing={rows}
        moving={moving}
        onClose={() => {
          setBooking(false)
          setMoving(null)
        }}
        onDone={(message, movedTo) => {
          setBooking(false)
          setMoving(null)
          setError(null)
          setNotice(message)
          if (movedTo !== day) setDay(movedTo)
          invalidate()
        }}
      />

      <CancelDialog
        appointment={cancelling}
        onClose={() => setCancelling(null)}
        onDone={(message) => {
          setCancelling(null)
          setError(null)
          setNotice(message)
          invalidate()
        }}
      />
    </>
  )
}

function toneFor(status: AppointmentStatusValue): 'neutral' | 'success' | 'warning' | 'danger' {
  if (status === 'ARRIVED') return 'warning'
  if (status === 'COMPLETED') return 'success'
  if (status === 'CANCELLED' || status === 'NO_SHOW') return 'danger'
  return 'neutral'
}

/**
 * Today, in the clinic's zone rather than the browser's.
 *
 * `toISOString()` would give the UTC date, which between seven in the
 * evening and midnight in Tashkent is yesterday — exactly when the clinic is
 * still seeing patients.
 */
function todayInClinic(): string {
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Tashkent',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).format(new Date())
  return parts
}

/** A day either side, by arithmetic on the date itself. */
function shiftDay(day: string, by: number): string {
  const [year = 1970, month = 1, date = 1] = day.split('-').map(Number)
  // Built in UTC and read back in UTC, so no timezone can shift the day by
  // one on the way through.
  const moved = new Date(Date.UTC(year, month - 1, date + by))
  return moved.toISOString().slice(0, 10)
}

function BookingDialog({
  open,
  day,
  existing,
  moving,
  onClose,
  onDone,
}: {
  open: boolean
  day: string
  existing: readonly Appointment[]
  /** Set when an existing booking is being moved rather than a new one made. */
  moving: Appointment | null
  onClose: () => void
  onDone: (message: string, movedTo: string) => void
}) {
  const { t } = useI18n()

  const [query, setQuery] = useState('')
  const [patientId, setPatientId] = useState('')
  const [dentistId, setDentistId] = useState('')
  const [on, setOn] = useState(day)
  const [at, setAt] = useState('')
  const [minutes, setMinutes] = useState('30')
  const [reason, setReason] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [opened, setOpened] = useState(false)

  const patients = usePatients(open && moving === null, { query })
  // Reception books the day and may not read the staff directory, so the
  // bookable dentists come from the clinic module behind the same permission
  // as the day itself. Asking `/users` here left the desk with an empty
  // picker and no way to name a dentist at all.
  const dentists = useClinicDentists(open)

  // Fill the form from the booking being moved, once per opening.
  if (open && !opened) {
    setOpened(true)
    if (moving !== null) {
      setPatientId(moving.patient_id)
      setDentistId(moving.dentist_user_id ?? '')
      setOn(moving.scheduled_on)
      setAt(formatClinicTime(moving.scheduled_at))
      setMinutes(moving.duration_minutes === null ? '' : String(moving.duration_minutes))
      setReason('')
    } else {
      setOn(day)
      setAt('')
      setMinutes('30')
      setPatientId('')
      setReason('')
    }
  }
  if (!open && opened) setOpened(false)

  const duration = minutes.trim() === '' ? null : Number(minutes)
  const proposed: Slot = {
    id: moving?.id,
    dentist_user_id: dentistId === '' ? null : dentistId,
    scheduled_on: on,
    scheduled_at: toApiTime(at),
    duration_minutes: duration !== null && Number.isFinite(duration) ? duration : null,
    status: 'SCHEDULED',
  }
  // Only meaningful while the day on screen is the day being booked: the
  // list in hand is that day's.
  const clash = on === day ? clashWith(proposed, existing as readonly Slot[]) : null
  const timeIsWrong = at.trim() !== '' && toApiTime(at) === null

  const submit = useMutation({
    mutationFn: () => {
      const body = {
        scheduled_on: on,
        scheduled_at: toApiTime(at),
        duration_minutes: proposed.duration_minutes,
        dentist_user_id: dentistId === '' ? null : dentistId,
        reason: reason.trim() === '' ? null : reason.trim(),
      }
      return moving !== null
        ? api.post<Appointment>(`/clinic/appointments/${moving.id}/reschedule`, body)
        : api.post<Appointment>('/clinic/appointments', {
            ...body,
            patient_id: patientId,
          })
    },
    onSuccess: () =>
      onDone(moving !== null ? t.appointments.moved : t.appointments.booked, on),
    onError: (caught) => setError(describeError(caught, t)),
  })

  const ready = on !== '' && !timeIsWrong && (moving !== null || patientId !== '')

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={moving !== null ? t.appointments.rescheduleTitle : t.appointments.bookTitle}
      closeLabel={t.common.close}
      footer={
        <>
          <Button onClick={onClose} disabled={submit.isPending}>
            {t.common.cancel}
          </Button>
          <Button
            id="appointment-submit"
            variant="primary"
            loading={submit.isPending}
            disabled={!ready}
            onClick={() => {
              setError(null)
              submit.mutate()
            }}
          >
            {moving !== null ? t.appointments.moveAction : t.appointments.bookAction}
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        {moving !== null ? (
          <p className="text-sm text-[var(--text-secondary)]">
            {moving.patient_name} · <span className="font-mono">{moving.patient_code}</span>
          </p>
        ) : (
          <>
            <Field
              label={t.appointments.findPatient}
              htmlFor="appointment-search"
              hint={t.appointments.findPatientHint}
            >
              <Input
                id="appointment-search"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
              />
            </Field>
            <Field label={t.appointments.patient} htmlFor="appointment-patient" required>
              <Select
                id="appointment-patient"
                value={patientId}
                onChange={(event) => setPatientId(event.target.value)}
              >
                <option value="">{t.clinic.choose}</option>
                {(patients.data ?? []).map((patient) => (
                  <option key={patient.id} value={patient.id}>
                    {patient.display_name} · {patient.code}
                  </option>
                ))}
              </Select>
            </Field>
          </>
        )}

        <Field label={t.appointments.dentist} htmlFor="appointment-dentist">
          <Select
            id="appointment-dentist"
            value={dentistId}
            onChange={(event) => setDentistId(event.target.value)}
          >
            <option value="">{t.appointments.anyDentist}</option>
            {(dentists.data ?? []).map((user) => (
              <option key={user.id} value={user.id}>
                {user.full_name}
              </option>
            ))}
          </Select>
        </Field>

        <div className="grid gap-4 sm:grid-cols-3">
          <Field label={t.appointments.day} htmlFor="appointment-date" required>
            <Input
              id="appointment-date"
              type="date"
              value={on}
              onChange={(event) => setOn(event.target.value)}
            />
          </Field>
          <Field
            label={t.appointments.time}
            htmlFor="appointment-time"
            hint={t.appointments.timeHint}
            error={timeIsWrong ? t.appointments.timeWrong : undefined}
          >
            <Input
              id="appointment-time"
              placeholder="15:00"
              value={at}
              onChange={(event) => setAt(event.target.value)}
            />
          </Field>
          <Field label={t.appointments.duration} htmlFor="appointment-minutes">
            <Input
              id="appointment-minutes"
              inputMode="numeric"
              value={minutes}
              onChange={(event) => setMinutes(event.target.value)}
            />
          </Field>
        </div>

        <Field label={t.appointments.reason} htmlFor="appointment-reason">
          <Input
            id="appointment-reason"
            placeholder={t.appointments.reasonPlaceholder}
            value={reason}
            onChange={(event) => setReason(event.target.value)}
          />
        </Field>

        {clash !== null ? (
          <Alert tone="warning">
            {t.appointments.clash
              .replace('{time}', formatClinicTime(clash.scheduled_at))
              .replace('{minutes}', String(clash.duration_minutes ?? 0))}
          </Alert>
        ) : null}
        {error ? <Alert tone="danger">{error}</Alert> : null}
      </div>
    </Dialog>
  )
}

function CancelDialog({
  appointment,
  onClose,
  onDone,
}: {
  appointment: Appointment | null
  onClose: () => void
  onDone: (message: string) => void
}) {
  const { t } = useI18n()
  const [reason, setReason] = useState('')
  const [error, setError] = useState<string | null>(null)

  const act = useMutation({
    mutationFn: () =>
      api.post(`/clinic/appointments/${appointment?.id}/status`, {
        status: 'CANCELLED',
        reason,
      }),
    onSuccess: () => {
      setReason('')
      onDone(t.appointments.cancelled)
    },
    onError: (caught) => setError(describeError(caught, t)),
  })

  return (
    <Dialog
      open={appointment !== null}
      onClose={onClose}
      title={t.appointments.cancelTitle}
      closeLabel={t.common.close}
      footer={
        <>
          <Button onClick={onClose} disabled={act.isPending}>
            {t.common.close}
          </Button>
          <Button
            id="appointment-cancel-submit"
            variant="danger"
            loading={act.isPending}
            disabled={reason.trim().length < 3}
            onClick={() => {
              setError(null)
              act.mutate()
            }}
          >
            {t.appointments.cancelAction}
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <p className="text-sm text-[var(--text-secondary)]">
          {t.appointments.cancelBody}
        </p>
        <Field label={t.appointments.reason} htmlFor="appointment-cancel-reason" required>
          <Input
            id="appointment-cancel-reason"
            value={reason}
            onChange={(event) => setReason(event.target.value)}
          />
        </Field>
        {error ? <Alert tone="danger">{error}</Alert> : null}
      </div>
    </Dialog>
  )
}
