import { useMemo, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useI18n } from '@/i18n'
import { useAuth } from '@/features/auth/AuthProvider'
import { api } from '@/lib/api'
import { useEmployees, usePayrollRun, usePayrollRuns, usePayslips } from '@/lib/queries'
import { describeError, errorRequestId } from '@/lib/errorMessage'
import { parseInputAmount } from '@/lib/money'
import { todayInClinic } from '@/lib/clinicDate'
import {
  ACTION_PERMISSION,
  canRecalculate,
  employerCost,
  isBlocked,
  isSettled,
  nextAction,
  periodCode,
  runTotals,
  splitLines,
  type RunAction,
} from '@/lib/payroll'
import type { Payslip, PayrollRun } from '@/lib/types'
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
  Textarea,
  Th,
} from '@/components/ui'

/**
 * The monthly job: open a month, calculate it, have it approved, post it, pay it.
 *
 * The screen offers exactly one act at a time, the one the run's status
 * allows, because the backend refuses any other — and a button that will be
 * refused is worse than no button. When a required statutory rule is
 * unapproved, the act is withheld and the reasons are shown verbatim as the
 * backend wrote them: they name the rule and the open question behind it.
 */
export function RunsPanel() {
  const { t } = useI18n()
  const { can } = useAuth()
  const [year, setYear] = useState(() => Number(todayInClinic().slice(0, 4)))
  const [selected, setSelected] = useState<string | null>(null)
  const runs = usePayrollRuns({ year }, true)

  const chosen = useMemo(
    () => runs.data?.find((run) => run.id === selected) ?? runs.data?.[0] ?? null,
    [runs.data, selected],
  )

  return (
    <div className="flex flex-col gap-5">
      <div className="grid gap-5 lg:grid-cols-[20rem_minmax(0,1fr)]">
        <Card>
          <CardHeader
            title={t.payroll.runs}
            actions={
              <label className="text-sm">
                <span className="sr-only">{t.payroll.year}</span>
                <Input
                  type="number"
                  aria-label={t.payroll.year}
                  value={year}
                  min={2000}
                  max={2100}
                  className="w-24"
                  onChange={(event) => setYear(Number(event.target.value))}
                />
              </label>
            }
          />
          {can('payroll.run.calculate') ? <OpenRunForm year={year} onOpened={setSelected} /> : null}
          {runs.isLoading ? (
            <LoadingState label={t.common.loading} />
          ) : runs.isError || !runs.data ? (
            <ErrorState
              title={t.errors.loadFailedTitle}
              message={describeError(runs.error, t)}
              requestId={errorRequestId(runs.error)}
              requestIdLabel={t.common.requestId}
              onRetry={() => void runs.refetch()}
              retryLabel={t.common.retry}
            />
          ) : runs.data.length === 0 ? (
            <EmptyState title={t.payroll.noRuns} body={t.payroll.noRunsBody} />
          ) : (
            <ul className="flex flex-col gap-1">
              {runs.data.map((run) => (
                <li key={run.id}>
                  <button
                    type="button"
                    onClick={() => setSelected(run.id)}
                    aria-current={chosen?.id === run.id || undefined}
                    className={
                      'flex w-full items-center justify-between gap-2 rounded-lg px-3 py-2 text-left text-sm ' +
                      (chosen?.id === run.id
                        ? 'bg-[var(--surface-sunken)] font-medium'
                        : 'hover:bg-[var(--surface-sunken)]')
                    }
                  >
                    <span>{periodCode(run)}</span>
                    <StatusBadge run={run} />
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Card>

        {chosen ? <RunDetail run={chosen} /> : <Card>{t.payroll.selectRun}</Card>}
      </div>
      {chosen ? <PayslipsCard run={chosen} /> : null}
    </div>
  )
}

function StatusBadge({ run }: { run: PayrollRun }) {
  const { t } = useI18n()
  const tone =
    run.status === 'PAID'
      ? 'success'
      : run.status === 'POSTED' || run.status === 'APPROVED'
        ? 'brand'
        : run.status === 'CANCELLED'
          ? 'danger'
          : isBlocked(run)
            ? 'warning'
            : 'neutral'
  return <Badge tone={tone}>{t.payroll[`status${run.status}`]}</Badge>
}

/** Opening a month records the working-day count the proration divides by. */
function OpenRunForm({ year, onOpened }: { year: number; onOpened: (id: string) => void }) {
  const { t } = useI18n()
  const queryClient = useQueryClient()
  const [month, setMonth] = useState(() => Number(todayInClinic().slice(5, 7)))
  const [workingDays, setWorkingDays] = useState('22')
  const [error, setError] = useState<string | null>(null)

  const open = useMutation({
    mutationFn: () =>
      api.post<PayrollRun>('/payroll/runs', {
        year,
        month,
        working_days: Number(workingDays),
      }),
    onSuccess: async (run) => {
      setError(null)
      await queryClient.invalidateQueries({ queryKey: ['payroll'] })
      onOpened(run.id)
    },
    onError: (err) => setError(describeError(err, t)),
  })

  return (
    <form
      className="mb-4 flex flex-col gap-3 border-b border-[var(--border-subtle)] pb-4"
      onSubmit={(event) => {
        event.preventDefault()
        open.mutate()
      }}
    >
      <div className="grid grid-cols-2 gap-3">
        <Field label={t.payroll.month} htmlFor="payroll-month">
          <Select
            id="payroll-month"
            value={month}
            onChange={(event) => setMonth(Number(event.target.value))}
          >
            {Array.from({ length: 12 }, (_, index) => index + 1).map((value) => (
              <option key={value} value={value}>
                {`${year}-${String(value).padStart(2, '0')}`}
              </option>
            ))}
          </Select>
        </Field>
        <Field label={t.payroll.workingDays} htmlFor="payroll-working-days" required>
          <Input
            id="payroll-working-days"
            type="number"
            min={1}
            max={31}
            value={workingDays}
            onChange={(event) => setWorkingDays(event.target.value)}
          />
        </Field>
      </div>
      <p className="text-xs text-[var(--text-muted)]">{t.payroll.workingDaysHint}</p>
      {error ? <Alert tone="danger">{error}</Alert> : null}
      <Button type="submit" variant="primary" loading={open.isPending}>
        {t.payroll.openRun}
      </Button>
    </form>
  )
}

function RunDetail({ run: listed }: { run: PayrollRun }) {
  const { t, formatDate } = useI18n()
  const { can } = useAuth()
  const queryClient = useQueryClient()
  // The list carries the reasons stored when the run was last calculated;
  // this endpoint re-asks the question now. They can differ — a rule
  // approved since is the ordinary case — and the fresher answer is the one
  // that decides what this screen offers.
  const fresh = usePayrollRun(listed.id, true)
  const run = fresh.data ?? listed
  const [error, setError] = useState<string | null>(null)
  const [adjusting, setAdjusting] = useState(false)
  const [cancelling, setCancelling] = useState(false)
  const employees = useEmployees({}, can('hr.employee.read'))

  const action = nextAction(run.status)
  const blocked = isBlocked(run)

  const advance = useMutation({
    mutationFn: (step: RunAction) => api.post(`/payroll/runs/${run.id}/${step}`, {}),
    onSuccess: async () => {
      setError(null)
      await queryClient.invalidateQueries({ queryKey: ['payroll'] })
      // Posting writes to the ledger, so the books on screen are now stale.
      await queryClient.invalidateQueries({ queryKey: ['accounting'] })
      await queryClient.invalidateQueries({ queryKey: ['reporting'] })
    },
    onError: (err) => setError(describeError(err, t)),
  })

  return (
    <div className="flex min-w-0 flex-col gap-5">
      <Card>
        <CardHeader
          title={`${t.payroll.period} ${periodCode(run)}`}
          description={`${run.code} · ${formatDate(run.period_start)} – ${formatDate(run.period_end)} · ${run.working_days} ${t.payroll.workingDays.toLowerCase()}`}
          actions={<StatusBadge run={run} />}
        />

        {blocked ? (
          <div className="mb-4 flex flex-col gap-2">
            <Alert tone="warning">
              <p className="font-medium">{t.payroll.blockedTitle}</p>
              <p className="mt-1">{t.payroll.blockedBody}</p>
              <ul className="mt-2 list-disc pl-5">
                {run.blocked_reasons.map((reason) => (
                  <li key={reason}>{reason}</li>
                ))}
              </ul>
            </Alert>
          </div>
        ) : null}

        {error ? (
          <div className="mb-4">
            <Alert tone="danger">{error}</Alert>
          </div>
        ) : null}

        <div className="flex flex-wrap items-center gap-2">
          {action && can(ACTION_PERMISSION[action]) ? (
            <Button
              variant="primary"
              loading={advance.isPending}
              // Calculating is always allowed: it commits nothing. Approving
              // and posting are withheld while a rule is unapproved, because
              // the server refuses them and the reason is already on screen.
              disabled={blocked && action !== 'calculate'}
              onClick={() => advance.mutate(action)}
            >
              {t.payroll[action]}
            </Button>
          ) : null}
          {run.status === 'CALCULATED' && can('payroll.run.calculate') ? (
            <Button loading={advance.isPending} onClick={() => advance.mutate('calculate')}>
              {t.payroll.recalculate}
            </Button>
          ) : null}
          {!isSettled(run.status) && run.status !== 'CANCELLED' && can('payroll.run.calculate') ? (
            <Button onClick={() => setCancelling(true)}>{t.payroll.cancelRun}</Button>
          ) : null}
          {canRecalculate(run.status) && can('payroll.run.calculate') ? (
            <Button onClick={() => setAdjusting(true)}>{t.payroll.addAdjustment}</Button>
          ) : null}
        </div>

        <p className="mt-3 text-xs text-[var(--text-muted)]">{t.payroll.fourActs}</p>
        {run.status === 'CALCULATED' ? (
          <p className="mt-1 text-xs text-[var(--text-muted)]">{t.payroll.cannotApproveOwn}</p>
        ) : null}
        {run.journal_entry_id ? (
          <p className="mt-2 text-sm">
            {t.payroll.journalEntry}: <code>{run.journal_entry_id}</code>
          </p>
        ) : null}
        {run.status === 'POSTED' || run.status === 'PAID' ? (
          <p className="mt-2 text-xs text-[var(--text-muted)]">{t.payroll.postedNotice}</p>
        ) : null}
      </Card>

      {adjusting ? (
        <AdjustmentDialog
          run={run}
          employees={(employees.data ?? []).map((row) => ({
            id: row.id,
            name: row.display_name,
          }))}
          onClose={() => setAdjusting(false)}
        />
      ) : null}
      {cancelling ? <CancelDialog run={run} onClose={() => setCancelling(false)} /> : null}
    </div>
  )
}

/**
 * The month's payslips, across the full width of the page.
 *
 * Deliberately not inside the column beside the run list: the table carries
 * seven figures per person, and the rightmost of them is what employing
 * somebody actually cost the clinic. In a narrow column that column is the
 * one that falls off the edge — a browser pass caught it doing exactly that
 * — and it is the last figure that should be hard to see.
 */
function PayslipsCard({ run }: { run: PayrollRun }) {
  const { t, formatMoney } = useI18n()
  const { can } = useAuth()
  const payslips = usePayslips(run.id, true)
  const employees = useEmployees({}, can('hr.employee.read'))

  const names = useMemo(() => {
    const map = new Map<string, string>()
    for (const employee of employees.data ?? []) map.set(employee.id, employee.display_name)
    return map
  }, [employees.data])

  const totals = useMemo(() => runTotals(payslips.data ?? []), [payslips.data])

  return (
    <Card>
      <CardHeader title={t.payroll.payslips} />
      <div className="mb-4">
        <Alert tone="info">{t.payroll.contributionNotice}</Alert>
      </div>
      {payslips.isLoading ? (
        <LoadingState label={t.common.loading} />
      ) : payslips.isError || !payslips.data ? (
        <ErrorState
          title={t.errors.loadFailedTitle}
          message={describeError(payslips.error, t)}
          requestId={errorRequestId(payslips.error)}
          requestIdLabel={t.common.requestId}
          onRetry={() => void payslips.refetch()}
          retryLabel={t.common.retry}
        />
      ) : payslips.data.length === 0 ? (
        <EmptyState title={t.payroll.noPayslips} body={t.payroll.noPayslipsBody} />
      ) : (
        <>
          <Table>
            <thead>
              <tr>
                <Th>{t.payroll.employee}</Th>
                <Th numeric>{t.payroll.workedDays}</Th>
                <Th numeric>{t.payroll.gross}</Th>
                <Th numeric>{t.payroll.deductions}</Th>
                <Th numeric>{t.payroll.net}</Th>
                <Th numeric>{t.payroll.contributions}</Th>
                <Th numeric>{t.payroll.employerCost}</Th>
              </tr>
            </thead>
            <tbody>
              {payslips.data.map((slip) => (
                <tr key={slip.id}>
                  <Td>{names.get(slip.employee_id) ?? slip.employee_id}</Td>
                  <Td numeric>{`${slip.worked_days} / ${slip.working_days}`}</Td>
                  <Td numeric>{formatMoney(slip.gross, run.currency)}</Td>
                  <Td numeric>{formatMoney(slip.employee_deductions, run.currency)}</Td>
                  <Td numeric>
                    <strong>{formatMoney(slip.net, run.currency)}</strong>
                  </Td>
                  <Td numeric>{formatMoney(slip.employer_contributions, run.currency)}</Td>
                  <Td numeric>{formatMoney(employerCost(slip), run.currency)}</Td>
                </tr>
              ))}
              <tr className="border-t-2 border-[var(--border)]">
                <Td colSpan={2}>
                  <strong>{t.payroll.totals}</strong>
                </Td>
                <Td numeric>
                  <strong>{formatMoney(totals.gross, run.currency)}</strong>
                </Td>
                <Td numeric>
                  <strong>{formatMoney(totals.deductions, run.currency)}</strong>
                </Td>
                <Td numeric>
                  <strong>{formatMoney(totals.net, run.currency)}</strong>
                </Td>
                <Td numeric>
                  <strong>{formatMoney(totals.contributions, run.currency)}</strong>
                </Td>
                <Td numeric>
                  <strong>{formatMoney(totals.cost, run.currency)}</strong>
                </Td>
              </tr>
            </tbody>
          </Table>
          <div className="mt-4 flex flex-col gap-4">
            {payslips.data.map((slip) => (
              <PayslipLines
                key={slip.id}
                slip={slip}
                currency={run.currency}
                name={names.get(slip.employee_id) ?? slip.employee_id}
              />
            ))}
          </div>
        </>
      )}
    </Card>
  )
}

/**
 * One payslip in full, with every line and the rule version behind it.
 *
 * The three sides are shown as three groups rather than one signed column,
 * because a single column of pluses and minuses is exactly where an employer
 * contribution starts looking like a deduction.
 */
function PayslipLines({ slip, currency, name }: { slip: Payslip; currency: string; name: string }) {
  const { t, formatMoney } = useI18n()
  const [open, setOpen] = useState(false)
  const split = splitLines(slip.lines)

  const groups = [
    { heading: t.payroll.earningsHeading, lines: split.earnings },
    { heading: t.payroll.deductionsHeading, lines: split.deductions },
    { heading: t.payroll.contributionsHeading, lines: split.contributions },
  ].filter((group) => group.lines.length > 0)

  return (
    <section className="rounded-xl border border-[var(--border-subtle)] p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-semibold text-[var(--text-primary)]">{name}</h3>
        <Button size="sm" onClick={() => setOpen((value) => !value)}>
          {open ? t.payroll.hideLines : t.payroll.showLines}
        </Button>
      </div>
      <p className="mt-1 text-xs text-[var(--text-muted)]">
        {`${t.payroll.monthlySalary}: ${formatMoney(slip.monthly_salary, currency)} · ${slip.worked_days} / ${slip.working_days}`}
      </p>
      {open ? (
        <div className="mt-3 flex flex-col gap-4">
          {groups.map((group) => (
            <div key={group.heading}>
              <h4 className="mb-2 text-xs font-semibold tracking-wide text-[var(--text-secondary)] uppercase">
                {group.heading}
              </h4>
              <Table>
                <thead>
                  <tr>
                    <Th>{t.payroll.lineRule}</Th>
                    <Th numeric>{t.payroll.lineRate}</Th>
                    <Th numeric>{t.payroll.amount}</Th>
                    <Th>{t.payroll.ruleVersion}</Th>
                  </tr>
                </thead>
                <tbody>
                  {group.lines.map((line) => (
                    <tr key={line.id}>
                      <Td>
                        {line.description}
                        {line.note ? (
                          <span className="block text-xs text-[var(--text-muted)]">
                            {line.note}
                          </span>
                        ) : null}
                      </Td>
                      <Td numeric>{line.rate === null ? '—' : `${line.rate}%`}</Td>
                      <Td numeric>{formatMoney(line.amount, currency)}</Td>
                      <Td>
                        {line.statutory_rule_id ? (
                          <code className="text-xs">{line.statutory_rule_id.slice(0, 8)}</code>
                        ) : (
                          '—'
                        )}
                      </Td>
                    </tr>
                  ))}
                </tbody>
              </Table>
            </div>
          ))}
        </div>
      ) : null}
    </section>
  )
}

/** A bonus or an agreed deduction. Neither is accepted without a reason. */
function AdjustmentDialog({
  run,
  employees,
  onClose,
}: {
  run: PayrollRun
  employees: { id: string; name: string }[]
  onClose: () => void
}) {
  const { t } = useI18n()
  const queryClient = useQueryClient()
  const [employeeId, setEmployeeId] = useState(employees[0]?.id ?? '')
  const [kind, setKind] = useState<'BONUS' | 'DEDUCTION'>('BONUS')
  const [amount, setAmount] = useState('')
  const [reason, setReason] = useState('')
  const [error, setError] = useState<string | null>(null)

  const parsed = parseInputAmount(amount)
  const amountInvalid = parsed === null || parsed === 0n
  const reasonMissing = reason.trim() === ''

  const save = useMutation({
    mutationFn: () =>
      api.post(`/payroll/runs/${run.id}/adjustments`, {
        employee_id: employeeId,
        kind,
        amount,
        reason: reason.trim(),
      }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['payroll'] })
      onClose()
    },
    onError: (err) => setError(describeError(err, t)),
  })

  return (
    <Dialog
      open
      onClose={onClose}
      title={t.payroll.addAdjustment}
      closeLabel={t.common.close}
      footer={
        <>
          <Button onClick={onClose} disabled={save.isPending}>
            {t.common.cancel}
          </Button>
          <Button
            variant="primary"
            loading={save.isPending}
            disabled={amountInvalid || reasonMissing || employeeId === ''}
            onClick={() => save.mutate()}
          >
            {t.common.save}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-3">
        <Field label={t.payroll.employee} htmlFor="adjust-employee" required>
          <Select
            id="adjust-employee"
            value={employeeId}
            onChange={(event) => setEmployeeId(event.target.value)}
          >
            {employees.map((employee) => (
              <option key={employee.id} value={employee.id}>
                {employee.name}
              </option>
            ))}
          </Select>
        </Field>
        <Field label={t.payroll.adjustmentKind} htmlFor="adjust-kind">
          <Select
            id="adjust-kind"
            value={kind}
            onChange={(event) => setKind(event.target.value as 'BONUS' | 'DEDUCTION')}
          >
            <option value="BONUS">{t.payroll.bonus}</option>
            <option value="DEDUCTION">{t.payroll.deduction}</option>
          </Select>
        </Field>
        <Field
          label={t.payroll.amount}
          htmlFor="adjust-amount"
          required
          error={amount !== '' && amountInvalid ? t.payroll.amountInvalid : undefined}
        >
          <Input
            id="adjust-amount"
            inputMode="decimal"
            value={amount}
            onChange={(event) => setAmount(event.target.value)}
          />
        </Field>
        <Field
          label={t.payroll.reason}
          htmlFor="adjust-reason"
          required
          hint={t.payroll.reasonHint}
          error={reason !== '' && reasonMissing ? t.payroll.reasonRequired : undefined}
        >
          <Textarea
            id="adjust-reason"
            value={reason}
            onChange={(event) => setReason(event.target.value)}
          />
        </Field>
        {error ? <Alert tone="danger">{error}</Alert> : null}
      </div>
    </Dialog>
  )
}

function CancelDialog({ run, onClose }: { run: PayrollRun; onClose: () => void }) {
  const { t } = useI18n()
  const queryClient = useQueryClient()
  const [reason, setReason] = useState('')
  const [error, setError] = useState<string | null>(null)

  const cancel = useMutation({
    mutationFn: () => api.post(`/payroll/runs/${run.id}/cancel`, { reason: reason.trim() }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['payroll'] })
      onClose()
    },
    onError: (err) => setError(describeError(err, t)),
  })

  return (
    <Dialog
      open
      onClose={onClose}
      title={t.payroll.cancelTitle}
      closeLabel={t.common.close}
      footer={
        <>
          <Button onClick={onClose} disabled={cancel.isPending}>
            {t.common.cancel}
          </Button>
          <Button
            variant="danger"
            loading={cancel.isPending}
            disabled={reason.trim() === ''}
            onClick={() => cancel.mutate()}
          >
            {t.common.confirm}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-3">
        <Field label={t.payroll.cancelReason} htmlFor="cancel-reason" required>
          <Textarea
            id="cancel-reason"
            value={reason}
            onChange={(event) => setReason(event.target.value)}
          />
        </Field>
        <p className="text-xs text-[var(--text-muted)]">{t.payroll.cancelReasonRequired}</p>
        {error ? <Alert tone="danger">{error}</Alert> : null}
      </div>
    </Dialog>
  )
}
