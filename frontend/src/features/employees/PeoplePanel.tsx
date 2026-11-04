import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useI18n } from '@/i18n'
import { useAuth } from '@/features/auth/AuthProvider'
import { api } from '@/lib/api'
import {
  useEmployee,
  useEmployees,
  useLinkableUsers,
  usePositions,
  useSchedule,
} from '@/lib/queries'
import { describeError, errorRequestId } from '@/lib/errorMessage'
import { employmentIsOver, nextEmploymentStatuses } from '@/lib/employees'
import { todayInClinic } from '@/lib/clinicDate'
import {
  Alert,
  Badge,
  Button,
  Card,
  CardHeader,
  DescriptionList,
  Dialog,
  EmptyState,
  ErrorState,
  Field,
  Input,
  LoadingState,
  Select,
  Table,
  Td,
  Textarea,
  Th,
} from '@/components/ui'
import type { Employee, EmploymentStatus } from '@/lib/types'

const WEEKDAYS = [1, 2, 3, 4, 5, 6, 7] as const

/**
 * Who works here.
 *
 * The screen's one structural decision: **an employee is not a user.** The
 * login is shown and can be linked, and the roles beside it are read from
 * that login's membership — but nothing here creates an account or grants a
 * role. Hiring somebody and giving them authority over the books are two
 * decisions, and this screen only makes the first.
 */
export function PeoplePanel() {
  const { t } = useI18n()
  const { can } = useAuth()
  const mayManage = can('hr.employee.manage')

  const [query, setQuery] = useState('')
  const [status, setStatus] = useState('')
  const [openId, setOpenId] = useState<string | null>(null)
  const [adding, setAdding] = useState(false)

  const employees = useEmployees(
    { q: query || undefined, status: status || undefined },
    true,
  )

  if (openId) {
    return (
      <EmployeeDetail
        employeeId={openId}
        mayManage={mayManage}
        onBack={() => setOpenId(null)}
      />
    )
  }

  return (
    <>
      <Card>
        <CardHeader
          title={t.employees.peopleTab}
          actions={
            <div className="flex flex-wrap items-center gap-2">
              <label className="sr-only" htmlFor="employee-search">
                {t.employees.search}
              </label>
              <Input
                id="employee-search"
                value={query}
                placeholder={t.employees.search}
                onChange={(event) => setQuery(event.target.value)}
                className="w-48"
              />
              <label className="sr-only" htmlFor="employee-status">
                {t.employees.status}
              </label>
              <Select
                id="employee-status"
                value={status}
                onChange={(event) => setStatus(event.target.value)}
                className="w-auto"
              >
                <option value="">{t.common.all}</option>
                {(['ACTIVE', 'PROSPECTIVE', 'SUSPENDED', 'ENDED'] as const).map((s) => (
                  <option key={s} value={s}>
                    {t.employees[`status${s}` as const]}
                  </option>
                ))}
              </Select>
              {mayManage ? (
                <Button size="sm" variant="primary" onClick={() => setAdding(true)}>
                  {t.employees.add}
                </Button>
              ) : null}
            </div>
          }
        />

        {employees.isLoading ? (
          <LoadingState label={t.common.loading} />
        ) : employees.isError ? (
          <ErrorState
            title={t.errors.loadFailedTitle}
            message={describeError(employees.error, t)}
            requestId={errorRequestId(employees.error)}
            requestIdLabel={t.common.requestId}
            onRetry={() => void employees.refetch()}
            retryLabel={t.common.retry}
          />
        ) : (employees.data ?? []).length === 0 ? (
          <EmptyState title={t.employees.none} />
        ) : (
          <Table>
            <thead>
              <tr>
                <Th>{t.employees.reference}</Th>
                <Th>{t.common.name}</Th>
                <Th>{t.employees.position}</Th>
                <Th>{t.employees.status}</Th>
                <Th>{t.employees.hiredOn}</Th>
                <Th>{t.common.actions}</Th>
              </tr>
            </thead>
            <tbody>
              {(employees.data ?? []).map((employee) => (
                <EmployeeRow
                  key={employee.id}
                  employee={employee}
                  onOpen={() => setOpenId(employee.id)}
                />
              ))}
            </tbody>
          </Table>
        )}
      </Card>

      {adding ? (
        <AddEmployeeDialog
          onClose={() => setAdding(false)}
          onCreated={(employee) => {
            setAdding(false)
            setOpenId(employee.id)
          }}
        />
      ) : null}
    </>
  )
}

function EmployeeRow({
  employee,
  onOpen,
}: {
  employee: Employee
  onOpen: () => void
}) {
  const { t, formatDate } = useI18n()
  return (
    <tr>
      <Td>
        <span className="font-mono text-xs">{employee.code}</span>
      </Td>
      <Td>{employee.display_name}</Td>
      <Td>{employee.position_name ?? '—'}</Td>
      <Td>
        <StatusBadge status={employee.status} />
      </Td>
      <Td>{formatDate(employee.hired_on)}</Td>
      <Td>
        <div className="flex justify-end">
          <Button size="sm" onClick={onOpen}>
            {t.employees.open}
          </Button>
        </div>
      </Td>
    </tr>
  )
}

function StatusBadge({ status }: { status: EmploymentStatus }) {
  const { t } = useI18n()
  const tone: Record<EmploymentStatus, 'neutral' | 'success' | 'warning'> = {
    PROSPECTIVE: 'neutral',
    ACTIVE: 'success',
    SUSPENDED: 'warning',
    ENDED: 'neutral',
  }
  return <Badge tone={tone[status]}>{t.employees[`status${status}` as const]}</Badge>
}

// ------------------------------------------------------------- one employee

function EmployeeDetail({
  employeeId,
  mayManage,
  onBack,
}: {
  employeeId: string
  mayManage: boolean
  onBack: () => void
}) {
  const { t, formatDate } = useI18n()
  const queryClient = useQueryClient()
  const employee = useEmployee(employeeId, true)
  const [error, setError] = useState<string | null>(null)
  const [editing, setEditing] = useState(false)
  const [ending, setEnding] = useState(false)

  const invalidate = () => void queryClient.invalidateQueries({ queryKey: ['hr'] })

  const move = useMutation({
    mutationFn: (status: EmploymentStatus) =>
      api.post(`/hr/employees/${employeeId}/status`, { status }),
    onSuccess: () => {
      setError(null)
      invalidate()
    },
    onError: (caught) => setError(describeError(caught, t)),
  })

  if (employee.isLoading) return <LoadingState label={t.common.loading} />
  if (employee.isError || !employee.data) {
    return (
      <ErrorState
        title={t.errors.loadFailedTitle}
        message={describeError(employee.error, t)}
        requestId={errorRequestId(employee.error)}
        requestIdLabel={t.common.requestId}
        onRetry={() => void employee.refetch()}
        retryLabel={t.common.retry}
      />
    )
  }

  const person = employee.data
  const over = employmentIsOver(person.status)

  return (
    <div className="space-y-4">
      <Button size="sm" onClick={onBack}>
        ← {t.employees.back}
      </Button>

      <Card>
        <CardHeader
          title={person.display_name}
          description={`${t.employees.reference}: ${person.code}`}
          actions={
            <div className="flex flex-wrap items-center gap-2">
              <StatusBadge status={person.status} />
              {mayManage && !over ? (
                <Button size="sm" onClick={() => setEditing(true)}>
                  {t.common.edit}
                </Button>
              ) : null}
            </div>
          }
        />
        <DescriptionList
          items={[
            { label: t.employees.position, value: person.position_name ?? '—' },
            { label: t.employees.phone, value: person.phone ?? '—' },
            { label: t.employees.address, value: person.address ?? '—' },
            { label: t.employees.hiredOn, value: formatDate(person.hired_on) },
            ...(person.ended_on
              ? [{ label: t.employees.endedOn, value: formatDate(person.ended_on) }]
              : []),
            {
              label: t.employees.login,
              value: person.user_id ? t.employees.linkLogin : t.employees.noLogin,
            },
            {
              label: t.employees.roles,
              value:
                person.role_codes.length > 0 ? (
                  <span className="flex flex-wrap gap-1">
                    {person.role_codes.map((role) => (
                      <Badge key={role} tone="brand">
                        {role}
                      </Badge>
                    ))}
                  </span>
                ) : (
                  t.employees.noRoles
                ),
            },
            ...(person.notes ? [{ label: t.employees.notes, value: person.notes }] : []),
          ]}
        />

        <div className="space-y-3 px-4 pb-4">
          {over ? (
            <Alert tone="info">
              {t.employees.endedNotice.replace(
                '{date}',
                formatDate(person.ended_on ?? ''),
              )}
              {person.ended_reason ? ` ${person.ended_reason}` : ''}
            </Alert>
          ) : null}

          {mayManage && !over ? (
            <div className="flex flex-wrap gap-2">
              {nextEmploymentStatuses(person.status).map((status) => (
                <Button
                  key={status}
                  size="sm"
                  disabled={move.isPending}
                  loading={move.isPending && move.variables === status}
                  onClick={() => move.mutate(status)}
                >
                  {t.employees[`to${status}` as 'toACTIVE' | 'toSUSPENDED']}
                </Button>
              ))}
              <Button size="sm" variant="danger" onClick={() => setEnding(true)}>
                {t.employees.end}
              </Button>
            </div>
          ) : null}

          {error ? <Alert tone="danger">{error}</Alert> : null}
        </div>
      </Card>

      <RotaCard employeeId={employeeId} mayManage={mayManage && !over} />

      {editing ? (
        <EditEmployeeDialog
          employee={person}
          onClose={() => setEditing(false)}
        />
      ) : null}
      {ending ? (
        <EndEmploymentDialog
          employee={person}
          onClose={() => setEnding(false)}
        />
      ) : null}
    </div>
  )
}

// -------------------------------------------------------------------- rota

function RotaCard({
  employeeId,
  mayManage,
}: {
  employeeId: string
  mayManage: boolean
}) {
  const { t } = useI18n()
  const queryClient = useQueryClient()
  const schedule = useSchedule(employeeId, true)
  const [draft, setDraft] = useState<Record<number, string> | null>(null)
  const [error, setError] = useState<string | null>(null)

  const save = useMutation({
    mutationFn: (days: { weekday: number; hours: string }[]) =>
      api.put(`/hr/employees/${employeeId}/schedule`, { days }),
    onSuccess: () => {
      setDraft(null)
      setError(null)
      void queryClient.invalidateQueries({ queryKey: ['hr'] })
    },
    onError: (caught) => setError(describeError(caught, t)),
  })

  const current: Record<number, string> = {}
  for (const day of schedule.data ?? []) current[day.weekday] = day.hours
  const shown = draft ?? current

  return (
    <Card>
      <CardHeader
        title={t.employees.rota}
        description={t.employees.rotaHint}
        actions={
          mayManage ? (
            draft ? (
              <div className="flex gap-2">
                <Button size="sm" onClick={() => setDraft(null)} disabled={save.isPending}>
                  {t.common.cancel}
                </Button>
                <Button
                  size="sm"
                  variant="primary"
                  loading={save.isPending}
                  onClick={() =>
                    save.mutate(
                      WEEKDAYS.filter((d) => (shown[d] ?? '').trim() !== '').map((d) => ({
                        weekday: d,
                        hours: (shown[d] ?? '').trim(),
                      })),
                    )
                  }
                >
                  {t.employees.save}
                </Button>
              </div>
            ) : (
              <Button size="sm" onClick={() => setDraft({ ...current })}>
                {t.common.edit}
              </Button>
            )
          ) : undefined
        }
      />

      <div className="px-4 pb-4">
        {schedule.isLoading ? (
          <LoadingState label={t.common.loading} />
        ) : draft ? (
          <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
            {WEEKDAYS.map((weekday) => (
              <Field
                key={weekday}
                label={t.employees[`weekday${weekday}` as const]}
                htmlFor={`rota-${weekday}`}
              >
                <Input
                  id={`rota-${weekday}`}
                  inputMode="decimal"
                  placeholder={t.employees.hours}
                  value={draft[weekday] ?? ''}
                  onChange={(event) =>
                    setDraft((d) => ({ ...(d ?? {}), [weekday]: event.target.value }))
                  }
                />
              </Field>
            ))}
          </div>
        ) : Object.keys(current).length === 0 ? (
          <p className="text-sm text-[var(--text-secondary)]">{t.employees.rotaEmpty}</p>
        ) : (
          <ul className="flex flex-wrap gap-2">
            {WEEKDAYS.filter((d) => current[d]).map((weekday) => (
              <li
                key={weekday}
                className="rounded-lg border border-[var(--border-subtle)] px-3 py-1.5 text-sm"
              >
                {t.employees[`weekday${weekday}` as const]} ·{' '}
                <span className="font-medium">{current[weekday]}</span>
              </li>
            ))}
          </ul>
        )}
        {error ? (
          <div className="mt-2">
            <Alert tone="danger">{error}</Alert>
          </div>
        ) : null}
      </div>
    </Card>
  )
}

// ------------------------------------------------------------------ dialogs

function PositionSelect({
  value,
  onChange,
  id,
}: {
  value: string
  onChange: (value: string) => void
  id: string
}) {
  const { t } = useI18n()
  const positions = usePositions(true)
  return (
    <Select id={id} value={value} onChange={(event) => onChange(event.target.value)}>
      <option value="">{t.employees.noPosition}</option>
      {(positions.data ?? []).map((position) => (
        <option key={position.id} value={position.id}>
          {position.name}
        </option>
      ))}
    </Select>
  )
}

function AddEmployeeDialog({
  onClose,
  onCreated,
}: {
  onClose: () => void
  onCreated: (employee: Employee) => void
}) {
  const { t } = useI18n()
  const queryClient = useQueryClient()
  const linkable = useLinkableUsers(true)
  const [familyName, setFamilyName] = useState('')
  const [givenName, setGivenName] = useState('')
  const [patronymic, setPatronymic] = useState('')
  const [phone, setPhone] = useState('')
  const [positionId, setPositionId] = useState('')
  const [userId, setUserId] = useState('')
  const [hiredOn, setHiredOn] = useState(todayInClinic())
  const [error, setError] = useState<string | null>(null)

  const create = useMutation({
    mutationFn: () =>
      api.post<Employee>('/hr/employees', {
        family_name: familyName.trim(),
        given_name: givenName.trim(),
        patronymic: patronymic.trim() || null,
        phone: phone.trim() || null,
        position_id: positionId || null,
        user_id: userId || null,
        hired_on: hiredOn,
      }),
    onSuccess: (employee) => {
      void queryClient.invalidateQueries({ queryKey: ['hr'] })
      onCreated(employee)
    },
    onError: (caught) => setError(describeError(caught, t)),
  })

  const canSubmit = familyName.trim() !== '' && givenName.trim() !== ''

  return (
    <Dialog
      open
      size="lg"
      onClose={onClose}
      title={t.employees.add}
      closeLabel={t.common.close}
      footer={
        <>
          <Button onClick={onClose} disabled={create.isPending}>
            {t.common.cancel}
          </Button>
          <Button
            variant="primary"
            disabled={!canSubmit || create.isPending}
            loading={create.isPending}
            onClick={() => create.mutate()}
          >
            {t.common.save}
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <p className="text-sm text-[var(--text-secondary)]">{t.employees.addBody}</p>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label={t.employees.familyName} required htmlFor="new-family">
            <Input
              id="new-family"
              value={familyName}
              onChange={(event) => setFamilyName(event.target.value)}
            />
          </Field>
          <Field label={t.employees.givenName} required htmlFor="new-given">
            <Input
              id="new-given"
              value={givenName}
              onChange={(event) => setGivenName(event.target.value)}
            />
          </Field>
          <Field label={t.employees.patronymic} htmlFor="new-patronymic">
            <Input
              id="new-patronymic"
              value={patronymic}
              onChange={(event) => setPatronymic(event.target.value)}
            />
          </Field>
          <Field label={t.employees.phone} htmlFor="new-phone">
            <Input
              id="new-phone"
              value={phone}
              onChange={(event) => setPhone(event.target.value)}
            />
          </Field>
          <Field label={t.employees.position} htmlFor="new-position">
            <PositionSelect id="new-position" value={positionId} onChange={setPositionId} />
          </Field>
          <Field label={t.employees.hiredOn} htmlFor="new-hired">
            <Input
              id="new-hired"
              type="date"
              value={hiredOn}
              onChange={(event) => setHiredOn(event.target.value)}
            />
          </Field>
        </div>
        <Field
          label={t.employees.linkLogin}
          hint={t.employees.linkHint}
          htmlFor="new-user"
        >
          <Select
            id="new-user"
            value={userId}
            onChange={(event) => setUserId(event.target.value)}
          >
            <option value="">{t.employees.noLogin}</option>
            {(linkable.data ?? []).map((user) => (
              <option key={user.id} value={user.id}>
                {user.full_name} — {user.email}
              </option>
            ))}
          </Select>
        </Field>
        {error ? <Alert tone="danger">{error}</Alert> : null}
      </div>
    </Dialog>
  )
}

function EditEmployeeDialog({
  employee,
  onClose,
}: {
  employee: Employee
  onClose: () => void
}) {
  const { t } = useI18n()
  const queryClient = useQueryClient()
  const linkable = useLinkableUsers(true)
  const [familyName, setFamilyName] = useState(employee.family_name)
  const [givenName, setGivenName] = useState(employee.given_name)
  const [patronymic, setPatronymic] = useState(employee.patronymic ?? '')
  const [phone, setPhone] = useState(employee.phone ?? '')
  const [address, setAddress] = useState(employee.address ?? '')
  const [positionId, setPositionId] = useState(employee.position_id ?? '')
  const [userId, setUserId] = useState(employee.user_id ?? '')
  const [notes, setNotes] = useState(employee.notes ?? '')
  const [error, setError] = useState<string | null>(null)

  const save = useMutation({
    mutationFn: () =>
      api.patch(`/hr/employees/${employee.id}`, {
        family_name: familyName.trim(),
        given_name: givenName.trim(),
        patronymic: patronymic.trim() || null,
        phone: phone.trim() || null,
        address: address.trim() || null,
        position_id: positionId || null,
        user_id: userId || null,
        notes: notes.trim() || null,
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['hr'] })
      onClose()
    },
    onError: (caught) => setError(describeError(caught, t)),
  })

  return (
    <Dialog
      open
      size="lg"
      onClose={onClose}
      title={employee.display_name}
      closeLabel={t.common.close}
      footer={
        <>
          <Button onClick={onClose} disabled={save.isPending}>
            {t.common.cancel}
          </Button>
          <Button
            variant="primary"
            disabled={
              familyName.trim() === '' || givenName.trim() === '' || save.isPending
            }
            loading={save.isPending}
            onClick={() => save.mutate()}
          >
            {t.common.save}
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label={t.employees.familyName} required htmlFor="edit-family">
            <Input
              id="edit-family"
              value={familyName}
              onChange={(event) => setFamilyName(event.target.value)}
            />
          </Field>
          <Field label={t.employees.givenName} required htmlFor="edit-given">
            <Input
              id="edit-given"
              value={givenName}
              onChange={(event) => setGivenName(event.target.value)}
            />
          </Field>
          <Field label={t.employees.patronymic} htmlFor="edit-patronymic">
            <Input
              id="edit-patronymic"
              value={patronymic}
              onChange={(event) => setPatronymic(event.target.value)}
            />
          </Field>
          <Field label={t.employees.phone} htmlFor="edit-phone">
            <Input
              id="edit-phone"
              value={phone}
              onChange={(event) => setPhone(event.target.value)}
            />
          </Field>
          <Field label={t.employees.position} htmlFor="edit-position">
            <PositionSelect
              id="edit-position"
              value={positionId}
              onChange={setPositionId}
            />
          </Field>
          <Field
            label={t.employees.linkLogin}
            hint={t.employees.linkHint}
            htmlFor="edit-user"
          >
            <Select
              id="edit-user"
              value={userId}
              onChange={(event) => setUserId(event.target.value)}
            >
              <option value="">{t.employees.noLogin}</option>
              {/*
                The one already linked is not in `linkable`, which lists
                those with no record — so it is added back here, or editing
                anything else would silently unlink them.
              */}
              {employee.user_id ? (
                <option value={employee.user_id}>{t.employees.linkLogin}</option>
              ) : null}
              {(linkable.data ?? []).map((user) => (
                <option key={user.id} value={user.id}>
                  {user.full_name} — {user.email}
                </option>
              ))}
            </Select>
          </Field>
        </div>
        <Field label={t.employees.address} htmlFor="edit-address">
          <Input
            id="edit-address"
            value={address}
            onChange={(event) => setAddress(event.target.value)}
          />
        </Field>
        <Field label={t.employees.notes} htmlFor="edit-notes">
          <Textarea
            id="edit-notes"
            rows={2}
            value={notes}
            onChange={(event) => setNotes(event.target.value)}
          />
        </Field>
        {error ? <Alert tone="danger">{error}</Alert> : null}
      </div>
    </Dialog>
  )
}

function EndEmploymentDialog({
  employee,
  onClose,
}: {
  employee: Employee
  onClose: () => void
}) {
  const { t } = useI18n()
  const queryClient = useQueryClient()
  const [endedOn, setEndedOn] = useState(todayInClinic())
  const [reason, setReason] = useState('')
  const [error, setError] = useState<string | null>(null)

  const end = useMutation({
    mutationFn: () =>
      api.post(`/hr/employees/${employee.id}/end`, {
        ended_on: endedOn,
        reason: reason.trim(),
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['hr'] })
      onClose()
    },
    onError: (caught) => setError(describeError(caught, t)),
  })

  return (
    <Dialog
      open
      onClose={onClose}
      title={t.employees.end}
      closeLabel={t.common.close}
      footer={
        <>
          <Button onClick={onClose} disabled={end.isPending}>
            {t.common.cancel}
          </Button>
          <Button
            variant="danger"
            disabled={reason.trim().length < 3 || endedOn === '' || end.isPending}
            loading={end.isPending}
            onClick={() => end.mutate()}
          >
            {t.employees.end}
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <p className="text-sm text-[var(--text-secondary)]">{t.employees.endBody}</p>
        <Field label={t.employees.endedOn} required htmlFor="end-date">
          <Input
            id="end-date"
            type="date"
            value={endedOn}
            onChange={(event) => setEndedOn(event.target.value)}
          />
        </Field>
        <Field label={t.clinic.reason} required htmlFor="end-reason">
          <Textarea
            id="end-reason"
            rows={2}
            value={reason}
            onChange={(event) => setReason(event.target.value)}
          />
        </Field>
        {error ? <Alert tone="danger">{error}</Alert> : null}
      </div>
    </Dialog>
  )
}
