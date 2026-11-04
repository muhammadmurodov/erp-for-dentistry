import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useI18n } from '@/i18n'
import { useAuth } from '@/features/auth/AuthProvider'
import { api } from '@/lib/api'
import { useAttendance, useEmployees } from '@/lib/queries'
import { describeError, errorRequestId } from '@/lib/errorMessage'
import { shiftMonth, todayInClinic } from '@/lib/clinicDate'
import {
  attendanceBlockedReason,
  codeCarriesHours,
  daysInMonth,
  isWeekend,
  isoDate,
} from '@/lib/employees'
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
  Select,
  Textarea,
} from '@/components/ui'
import type { AttendanceCode, AttendanceDay, Employee } from '@/lib/types'

const CODES: readonly AttendanceCode[] = [
  'WORKED',
  'ABSENT',
  'SICK',
  'LEAVE',
  'HOLIDAY',
]

/**
 * The month, one row per person, one cell per day.
 *
 * A grid rather than a form per day because that is the shape of the
 * question people actually ask — "who was in last week" — and because a
 * clinic of three fits on one screen. Each cell is a button: pressing it
 * records or corrects that day.
 *
 * The month is the **clinic's** month. Taken from `todayInClinic()`, not from
 * the browser's date, because between seven and midnight in Tashkent a
 * UTC-derived month is the previous one on the first of the month.
 *
 * Nothing here is money. Payroll will read these rows, but it is payroll that
 * decides what a day is worth, and payroll that posts.
 */
export function TimesheetPanel() {
  const { t } = useI18n()
  const { can } = useAuth()
  const mayRecord = can('hr.attendance.record')

  const today = todayInClinic()
  const [year, setYear] = useState(Number(today.slice(0, 4)))
  const [month, setMonth] = useState(Number(today.slice(5, 7)))
  const [editing, setEditing] = useState<{
    employee: Employee
    date: string
    existing: AttendanceDay | null
  } | null>(null)

  const employees = useEmployees({}, true)
  const first = isoDate(year, month, 1)
  const last = isoDate(year, month, daysInMonth(year, month))
  const attendance = useAttendance({ from_date: first, to_date: last }, true)

  const step = (by: number) => {
    const moved = shiftMonth(year, month, by)
    setYear(moved.year)
    setMonth(moved.month)
  }

  if (employees.isLoading || attendance.isLoading) {
    return <LoadingState label={t.common.loading} />
  }
  if (employees.isError || attendance.isError) {
    const failed = employees.isError ? employees : attendance
    return (
      <ErrorState
        title={t.errors.loadFailedTitle}
        message={describeError(failed.error, t)}
        requestId={errorRequestId(failed.error)}
        requestIdLabel={t.common.requestId}
        onRetry={() => void failed.refetch()}
        retryLabel={t.common.retry}
      />
    )
  }

  // Somebody who left before this month, or starts after it, has no row:
  // every cell would be refused, and a row of refusals is not information.
  const people = (employees.data ?? []).filter(
    (e) => e.hired_on <= last && (e.ended_on === null || e.ended_on >= first),
  )
  const days = daysInMonth(year, month)

  const recorded = new Map<string, AttendanceDay>()
  for (const day of attendance.data ?? []) {
    recorded.set(`${day.employee_id}|${day.on_date}`, day)
  }

  return (
    <>
      <Card>
        <CardHeader
          title={t.employees.attendanceTab}
          description={t.employees.attendanceHint}
          actions={
            <div className="flex items-center gap-2">
              <Button size="sm" onClick={() => step(-1)} aria-label={t.employees.previousMonth}>
                ←
              </Button>
              <span className="min-w-24 text-center text-sm font-medium">
                {String(month).padStart(2, '0')}.{year}
              </span>
              <Button size="sm" onClick={() => step(1)} aria-label={t.employees.nextMonth}>
                →
              </Button>
            </div>
          }
        />

        {!mayRecord ? (
          <div className="px-4 pb-3">
            <Alert tone="info">{t.employees.attendanceNoAccess}</Alert>
          </div>
        ) : null}

        {people.length === 0 ? (
          <EmptyState title={t.employees.none} />
        ) : (
          <div className="overflow-x-auto px-4 pb-4">
            <table className="w-full border-collapse text-sm">
              <thead>
                <tr>
                  <th className="sticky left-0 z-10 bg-[var(--surface-raised)] px-2 py-1 text-left font-medium">
                    {t.common.name}
                  </th>
                  {Array.from({ length: days }, (_, i) => i + 1).map((day) => (
                    <th
                      key={day}
                      scope="col"
                      className="px-0.5 py-1 text-center text-xs font-normal text-[var(--text-muted)]"
                      style={
                        isWeekend(year, month, day)
                          ? { background: 'var(--surface-sunken)' }
                          : undefined
                      }
                    >
                      {day}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {people.map((employee) => (
                  <tr key={employee.id}>
                    <th
                      scope="row"
                      className="sticky left-0 z-10 max-w-40 truncate bg-[var(--surface-raised)] px-2 py-1 text-left font-normal"
                    >
                      {employee.display_name}
                    </th>
                    {Array.from({ length: days }, (_, i) => i + 1).map((day) => {
                      const date = isoDate(year, month, day)
                      const existing = recorded.get(`${employee.id}|${date}`) ?? null
                      const blocked = attendanceBlockedReason(
                        date,
                        today,
                        employee,
                        null,
                      )
                      return (
                        <td key={day} className="p-0.5 text-center">
                          <DayCell
                            code={existing?.code ?? null}
                            weekend={isWeekend(year, month, day)}
                            disabled={!mayRecord || blocked !== null}
                            label={`${employee.display_name} ${date}`}
                            onClick={() =>
                              setEditing({ employee, date, existing })
                            }
                          />
                        </td>
                      )
                    })}
                  </tr>
                ))}
              </tbody>
            </table>

            <Legend />
          </div>
        )}
      </Card>

      {editing ? (
        <RecordDayDialog
          employee={editing.employee}
          date={editing.date}
          existing={editing.existing}
          today={today}
          onClose={() => setEditing(null)}
        />
      ) : null}
    </>
  )
}

function DayCell({
  code,
  weekend,
  disabled,
  label,
  onClick,
}: {
  code: AttendanceCode | null
  weekend: boolean
  disabled: boolean
  label: string
  onClick: () => void
}) {
  const { t } = useI18n()
  const background = code
    ? {
        WORKED: 'var(--success)',
        ABSENT: 'var(--danger)',
        SICK: 'var(--warning)',
        LEAVE: 'var(--color-brand-200)',
        HOLIDAY: 'var(--surface-sunken)',
      }[code]
    : weekend
      ? 'var(--surface-sunken)'
      : undefined
  const colour = code === 'WORKED' || code === 'ABSENT' ? 'white' : undefined

  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      aria-label={`${label}: ${code ? t.employees[`code${code}` as const] : t.employees.notRecorded}`}
      title={code ? t.employees[`code${code}` as const] : t.employees.notRecorded}
      className="size-6 rounded border border-[var(--border-subtle)] text-[11px] leading-none disabled:opacity-40"
      style={{ background, color: colour }}
    >
      {code ? t.employees[`short${code}` as const] : ''}
    </button>
  )
}

function Legend() {
  const { t } = useI18n()
  return (
    <div className="mt-3 space-y-2">
      <ul className="flex flex-wrap gap-x-4 gap-y-1">
        {CODES.map((code) => (
          <li
            key={code}
            className="flex items-center gap-1.5 text-xs text-[var(--text-secondary)]"
          >
            <span className="font-mono">{t.employees[`short${code}` as const]}</span>
            {t.employees[`code${code}` as const]}
          </li>
        ))}
      </ul>
      {/*
        Said on the screen, not only in a document: these are not the twelve
        statutory letters, and nobody should copy this grid onto that form.
      */}
      <p className="text-xs text-[var(--text-muted)]">
        {t.employees.statutoryTimesheetNote}
      </p>
    </div>
  )
}

function RecordDayDialog({
  employee,
  date,
  existing,
  today,
  onClose,
}: {
  employee: Employee
  date: string
  existing: AttendanceDay | null
  today: string
  onClose: () => void
}) {
  const { t, formatDate } = useI18n()
  const queryClient = useQueryClient()
  const [code, setCode] = useState<AttendanceCode>(existing?.code ?? 'WORKED')
  const [hours, setHours] = useState(existing?.hours ?? '8')
  const [note, setNote] = useState(existing?.note ?? '')
  const [error, setError] = useState<string | null>(null)

  const invalidate = () => void queryClient.invalidateQueries({ queryKey: ['hr'] })
  const blocked = attendanceBlockedReason(date, today, employee, code)

  const record = useMutation({
    mutationFn: () =>
      api.post('/hr/attendance', {
        employee_id: employee.id,
        on_date: date,
        code,
        hours: codeCarriesHours(code) ? hours.trim() : null,
        note: note.trim() || null,
      }),
    onSuccess: () => {
      invalidate()
      onClose()
    },
    onError: (caught) => setError(describeError(caught, t)),
  })

  const clear = useMutation({
    mutationFn: () =>
      api.delete(
        `/hr/attendance?employee_id=${employee.id}&on_date=${date}`,
      ),
    onSuccess: () => {
      invalidate()
      onClose()
    },
    onError: (caught) => setError(describeError(caught, t)),
  })

  const blockedMessage = blocked
    ? {
        beforeHired: t.employees.blockedBeforeHired,
        afterLeft: t.employees.blockedAfterLeft,
        notStarted: t.employees.blockedNotStarted,
        future: t.employees.blockedFuture,
      }[blocked]
    : null

  return (
    <Dialog
      open
      onClose={onClose}
      title={t.employees.recordDay.replace('{date}', formatDate(date))}
      closeLabel={t.common.close}
      footer={
        <>
          {existing ? (
            <Button
              variant="ghost"
              onClick={() => clear.mutate()}
              loading={clear.isPending}
              disabled={record.isPending}
            >
              {t.employees.clearDay}
            </Button>
          ) : null}
          <Button onClick={onClose} disabled={record.isPending || clear.isPending}>
            {t.common.cancel}
          </Button>
          <Button
            variant="primary"
            disabled={
              blocked !== null ||
              record.isPending ||
              (codeCarriesHours(code) && hours.trim() === '')
            }
            loading={record.isPending}
            onClick={() => record.mutate()}
          >
            {t.common.save}
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <p className="text-sm text-[var(--text-secondary)]">{employee.display_name}</p>

        <Field label={t.employees.status} htmlFor="day-code">
          <Select
            id="day-code"
            value={code}
            onChange={(event) => setCode(event.target.value as AttendanceCode)}
          >
            {CODES.map((option) => (
              <option key={option} value={option}>
                {t.employees[`code${option}` as const]}
              </option>
            ))}
          </Select>
        </Field>

        {/* Hours belong to a day that was worked, and to no other. */}
        {codeCarriesHours(code) ? (
          <Field label={t.employees.hours} required htmlFor="day-hours">
            <Input
              id="day-hours"
              inputMode="decimal"
              value={hours}
              onChange={(event) => setHours(event.target.value)}
            />
          </Field>
        ) : null}

        <Field label={t.employees.notes} htmlFor="day-note">
          <Textarea
            id="day-note"
            rows={2}
            value={note}
            onChange={(event) => setNote(event.target.value)}
          />
        </Field>

        {blockedMessage ? <Alert tone="warning">{blockedMessage}</Alert> : null}
        {error ? <Alert tone="danger">{error}</Alert> : null}
      </div>
    </Dialog>
  )
}
