import { useEffect, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useI18n } from '@/i18n'
import { useAuth } from '@/features/auth/AuthProvider'
import { api } from '@/lib/api'
import { useCompensation, useEmployees } from '@/lib/queries'
import { describeError, errorRequestId } from '@/lib/errorMessage'
import { parseInputAmount } from '@/lib/money'
import { todayInClinic } from '@/lib/clinicDate'
import { salaryOn } from '@/lib/payroll'
import type { Compensation } from '@/lib/types'
import {
  Alert,
  Button,
  Card,
  CardHeader,
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
 * What the clinic agreed to pay each person, and from when.
 *
 * A rise is a new row rather than an edit of the old one, so this screen is a
 * history and not a form with a single salary box. The earlier agreement has
 * to stay readable: the payslips of past months were computed against it, and
 * a payslip whose salary has since been overwritten is a payslip nobody can
 * check.
 *
 * Which also means "the salary" is always a question about a date. The
 * current figure shown here is the one in force today, which may not be the
 * newest row — a rise agreed now and starting next month is both newer and
 * not yet in force.
 */
export function SalariesPanel() {
  const { t, formatMoney } = useI18n()
  const { can } = useAuth()
  const [employeeId, setEmployeeId] = useState<string | null>(null)
  const employees = useEmployees({ status: 'ACTIVE' }, can('hr.employee.read'))
  const today = todayInClinic()

  // The first employee, once they arrive, so the panel is never empty-looking
  // when there is in fact somebody to show.
  useEffect(() => {
    if (employeeId === null && employees.data && employees.data.length > 0) {
      setEmployeeId(employees.data[0]?.id ?? null)
    }
  }, [employees.data, employeeId])

  const agreements = useCompensation(employeeId, employeeId !== null)

  if (!can('hr.employee.read')) {
    return (
      <Card>
        <Alert tone="warning">{t.payroll.noAccess}</Alert>
      </Card>
    )
  }

  if (employees.isLoading) return <LoadingState label={t.common.loading} />
  if (employees.isError || !employees.data) {
    return (
      <ErrorState
        title={t.errors.loadFailedTitle}
        message={describeError(employees.error, t)}
        requestId={errorRequestId(employees.error)}
        requestIdLabel={t.common.requestId}
        onRetry={() => void employees.refetch()}
        retryLabel={t.common.retry}
      />
    )
  }
  if (employees.data.length === 0) {
    return <EmptyState title={t.payroll.noEmployees} body={t.payroll.noEmployeesBody} />
  }

  const current = salaryOn(agreements.data ?? [], today)

  return (
    <div className="flex flex-col gap-5">
      <Card>
        <CardHeader
          title={t.payroll.salaries}
          description={t.payroll.salariesSubtitle}
          actions={
            <label className="text-sm">
              <span className="sr-only">{t.payroll.selectEmployee}</span>
              <Select
                aria-label={t.payroll.selectEmployee}
                value={employeeId ?? ''}
                onChange={(event) => setEmployeeId(event.target.value)}
              >
                {employees.data.map((employee) => (
                  <option key={employee.id} value={employee.id}>
                    {employee.display_name}
                  </option>
                ))}
              </Select>
            </label>
          }
        />
        <p className="text-sm">
          {t.payroll.currentSalary}:{' '}
          <strong>{current === null ? t.payroll.noSalary : formatMoney(current)}</strong>
        </p>
        {current === null ? (
          <p className="mt-1 text-xs text-[var(--text-muted)]">{t.payroll.noSalaryBody}</p>
        ) : null}
      </Card>

      {can('payroll.rules.manage') && employeeId ? <SetSalaryForm employeeId={employeeId} /> : null}

      <Card>
        <CardHeader title={t.payroll.salaryHistory} description={t.payroll.riseIsNewRow} />
        {agreements.isLoading ? (
          <LoadingState label={t.common.loading} />
        ) : agreements.isError || !agreements.data ? (
          <ErrorState
            title={t.errors.loadFailedTitle}
            message={describeError(agreements.error, t)}
            requestId={errorRequestId(agreements.error)}
            requestIdLabel={t.common.requestId}
            onRetry={() => void agreements.refetch()}
            retryLabel={t.common.retry}
          />
        ) : agreements.data.length === 0 ? (
          <EmptyState title={t.payroll.noSalary} body={t.payroll.noSalaryBody} />
        ) : (
          <Table>
            <thead>
              <tr>
                <Th numeric>{t.payroll.salary}</Th>
                <Th>{t.payroll.effectiveFrom}</Th>
                <Th>{t.payroll.effectiveTo}</Th>
                <Th>{t.payroll.salaryNote}</Th>
              </tr>
            </thead>
            <tbody>
              {[...agreements.data]
                .sort((a, b) => b.effective_from.localeCompare(a.effective_from))
                .map((row: Compensation) => (
                  <tr key={row.id}>
                    <Td numeric>
                      <strong>{formatMoney(row.amount, row.currency)}</strong>
                    </Td>
                    <Td>{row.effective_from}</Td>
                    <Td>{row.effective_to ?? t.payroll.openEnded}</Td>
                    <Td>
                      <span className="text-xs">{row.note ?? '—'}</span>
                    </Td>
                  </tr>
                ))}
            </tbody>
          </Table>
        )}
      </Card>
    </div>
  )
}

function SetSalaryForm({ employeeId }: { employeeId: string }) {
  const { t } = useI18n()
  const queryClient = useQueryClient()
  const [amount, setAmount] = useState('')
  const [from, setFrom] = useState(() => `${todayInClinic().slice(0, 7)}-01`)
  const [note, setNote] = useState('')
  const [error, setError] = useState<string | null>(null)

  const parsed = parseInputAmount(amount)
  const invalid = parsed === null || parsed === 0n

  const save = useMutation({
    mutationFn: () =>
      api.post(`/payroll/employees/${employeeId}/compensation`, {
        // Sent as the typed string: a salary read as a float is a salary
        // nobody can reconcile to the agreement that was signed.
        amount: amount.trim().replace(',', '.'),
        effective_from: from,
        note: note.trim() === '' ? null : note.trim(),
      }),
    onSuccess: async () => {
      setError(null)
      setAmount('')
      setNote('')
      await queryClient.invalidateQueries({ queryKey: ['payroll'] })
    },
    onError: (err) => setError(describeError(err, t)),
  })

  return (
    <Card>
      <CardHeader title={t.payroll.setSalary} />
      <form
        className="grid gap-3 sm:grid-cols-3"
        onSubmit={(event) => {
          event.preventDefault()
          save.mutate()
        }}
      >
        <Field
          label={t.payroll.salary}
          htmlFor="salary-amount"
          required
          error={amount !== '' && invalid ? t.payroll.amountInvalid : undefined}
        >
          <Input
            id="salary-amount"
            inputMode="decimal"
            value={amount}
            onChange={(event) => setAmount(event.target.value)}
          />
        </Field>
        <Field label={t.payroll.salaryFrom} htmlFor="salary-from" required>
          <Input
            id="salary-from"
            type="date"
            value={from}
            onChange={(event) => setFrom(event.target.value)}
          />
        </Field>
        <Field label={t.payroll.salaryNote} htmlFor="salary-note" hint={t.common.optional}>
          <Input id="salary-note" value={note} onChange={(event) => setNote(event.target.value)} />
        </Field>
        <div className="sm:col-span-3 flex flex-col gap-2">
          {error ? <Alert tone="danger">{error}</Alert> : null}
          <Button
            type="submit"
            variant="primary"
            loading={save.isPending}
            disabled={invalid || from === ''}
            className="self-start"
          >
            {t.payroll.setSalary}
          </Button>
        </div>
      </form>
    </Card>
  )
}
