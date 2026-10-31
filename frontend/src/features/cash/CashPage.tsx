import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useI18n } from '@/i18n'
import { useAuth } from '@/features/auth/AuthProvider'
import { api } from '@/lib/api'
import {
  useCashRegisters,
  useCashSession,
  useCashSessions,
  useCustomers,
} from '@/lib/queries'
import { describeError, errorRequestId } from '@/lib/errorMessage'
import {
  Alert,
  Badge,
  Button,
  Card,
  CardHeader,
  Checkbox,
  Dialog,
  EmptyState,
  ErrorState,
  Field,
  Input,
  LoadingState,
  PageHeader,
  Select,
  Table,
  Td,
  Th,
} from '@/components/ui'
import type {
  CashRegister,
  CashSession,
  CashSessionDetail,
  DepositRoute,
  MovementKind,
  SessionStatus,
} from '@/lib/types'

function StatusBadge({ status }: { status: SessionStatus }) {
  const { t } = useI18n()
  if (status === 'CLOSED') return <Badge>{t.cash.closed}</Badge>
  if (status === 'COUNTED') return <Badge tone="warning">{t.cash.counted}</Badge>
  return <Badge tone="success">{t.cash.opened}</Badge>
}

export function CashPage() {
  const { t, formatDate } = useI18n()
  const { can } = useAuth()
  const queryClient = useQueryClient()

  const mayRead = can('cash.register.read')
  const mayManageRegisters = can('cash.register.manage')
  const mayOperate = can('cash.session.operate')

  const registers = useCashRegisters(mayRead)
  const sessions = useCashSessions(mayRead)
  const [selected, setSelected] = useState<string | null>(null)
  const [registerOpen, setRegisterOpen] = useState(false)
  const [openSessionOpen, setOpenSessionOpen] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const invalidate = () => void queryClient.invalidateQueries({ queryKey: ['cash'] })

  if (!mayRead) {
    return (
      <>
        <PageHeader title={t.cash.title} />
        <Alert tone="warning">{t.moduleStatus.noAccess}</Alert>
      </>
    )
  }
  if (registers.isLoading || sessions.isLoading) {
    return <LoadingState label={t.common.loading} />
  }
  if (registers.isError) {
    return (
      <ErrorState
        title={t.errors.loadFailedTitle}
        message={describeError(registers.error, t)}
        requestId={errorRequestId(registers.error)}
        requestIdLabel={t.common.requestId}
        onRetry={() => void registers.refetch()}
        retryLabel={t.common.retry}
      />
    )
  }

  const registerRows = registers.data ?? []
  const sessionRows = sessions.data ?? []
  const openSession = sessionRows.find((s) => s.status === 'OPEN') ?? null
  const active = selected ?? openSession?.id ?? null

  return (
    <>
      <PageHeader
        title={t.cash.title}
        actions={
          <>
            {mayManageRegisters ? (
              <Button size="sm" onClick={() => setRegisterOpen(true)}>
                {t.cash.newRegister}
              </Button>
            ) : null}
            {mayOperate && registerRows.length > 0 && !openSession ? (
              <Button
                variant="primary"
                size="sm"
                onClick={() => setOpenSessionOpen(true)}
              >
                {t.cash.openSession}
              </Button>
            ) : null}
          </>
        }
      />

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

      {registerRows.length === 0 ? (
        <EmptyState
          title={t.cash.emptyRegisters}
          body={mayManageRegisters ? undefined : t.moduleStatus.noAccess}
          action={
            mayManageRegisters ? (
              <Button variant="primary" onClick={() => setRegisterOpen(true)}>
                {t.cash.newRegister}
              </Button>
            ) : undefined
          }
        />
      ) : (
        <div className="grid gap-4 lg:grid-cols-[20rem_1fr]">
          <Card>
            <CardHeader title={t.cash.sessions} />
            {sessionRows.length === 0 ? (
              <EmptyState title={t.cash.emptySessions} body={t.cash.openFirst} />
            ) : (
              <ul className="flex flex-col gap-1">
                {sessionRows.map((session: CashSession) => (
                  <li key={session.id}>
                    <button
                      type="button"
                      onClick={() => setSelected(session.id)}
                      className={
                        'flex w-full items-center justify-between gap-2 rounded-lg px-3 py-2 text-left text-sm ' +
                        (session.id === active
                          ? 'bg-brand-50 text-brand-800'
                          : 'hover:bg-[var(--surface-sunken)]')
                      }
                    >
                      <span>{formatDate(session.business_date)}</span>
                      <StatusBadge status={session.status} />
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </Card>

          {active ? (
            <SessionDetail
              sessionId={active}
              onChanged={(message) => {
                setError(null)
                setNotice(message)
                invalidate()
              }}
              onError={(message) => setError(message)}
            />
          ) : (
            <Card>
              <EmptyState title={t.cash.noOpenSession} body={t.cash.openFirst} />
            </Card>
          )}
        </div>
      )}

      <CreateRegisterDialog
        open={registerOpen}
        onClose={() => setRegisterOpen(false)}
        onDone={() => {
          setRegisterOpen(false)
          setNotice(t.cash.registerCreated)
          invalidate()
        }}
        onError={(message) => {
          setRegisterOpen(false)
          setError(message)
        }}
      />

      <OpenSessionDialog
        open={openSessionOpen}
        registers={registerRows}
        onClose={() => setOpenSessionOpen(false)}
        onDone={(session) => {
          setOpenSessionOpen(false)
          setSelected(session.id)
          setNotice(t.cash.sessionOpened)
          invalidate()
        }}
        onError={(message) => {
          setOpenSessionOpen(false)
          setError(message)
        }}
      />
    </>
  )
}

function SessionDetail({
  sessionId,
  onChanged,
  onError,
}: {
  sessionId: string
  onChanged: (message: string) => void
  onError: (message: string) => void
}) {
  const { t, formatDate, formatDateTime } = useI18n()
  const { can } = useAuth()
  const session = useCashSession(sessionId)
  const [paymentOpen, setPaymentOpen] = useState(false)
  const [expenseOpen, setExpenseOpen] = useState(false)
  const [depositOpen, setDepositOpen] = useState(false)
  const [countOpen, setCountOpen] = useState(false)

  const close = useMutation({
    mutationFn: () =>
      api.post<CashSessionDetail>(`/cash/sessions/${sessionId}/close`),
    onSuccess: () => onChanged(t.cash.sessionClosed),
    onError: (err) => onError(describeError(err, t)),
  })

  if (session.isLoading) return <LoadingState label={t.common.loading} />
  if (!session.data) return null

  const data = session.data
  const isOpen = data.status === 'OPEN'
  const isCounted = data.status === 'COUNTED'
  const kindLabel: Record<MovementKind, string> = {
    OPENING_FLOAT: t.cash.openingFloat,
    RECEIPT: t.cash.receipts,
    REFUND: t.cash.refunds,
    EXPENSE: t.cash.expenses,
    DEPOSIT: t.cash.deposits,
    ADJUSTMENT_IN: t.cash.amount,
    ADJUSTMENT_OUT: t.cash.amount,
  }

  return (
    <>
      <Card>
        <CardHeader
          title={`${data.register_name} · ${formatDate(data.business_date)}`}
          description={t.cash.expectedHint}
          actions={
            <>
              {isOpen && can('sales.payment.record') ? (
                <Button size="sm" variant="primary" onClick={() => setPaymentOpen(true)}>
                  {t.cash.takePayment}
                </Button>
              ) : null}
              {isOpen && can('cash.session.operate') ? (
                <>
                  <Button size="sm" onClick={() => setExpenseOpen(true)}>
                    {t.cash.recordExpense}
                  </Button>
                  <Button size="sm" onClick={() => setDepositOpen(true)}>
                    {t.cash.recordDeposit}
                  </Button>
                </>
              ) : null}
              {isOpen && can('cash.count.submit') ? (
                <Button size="sm" variant="primary" onClick={() => setCountOpen(true)}>
                  {t.cash.submitCount}
                </Button>
              ) : null}
              {isCounted && can('cash.session.operate') ? (
                <Button
                  size="sm"
                  variant="primary"
                  loading={close.isPending}
                  onClick={() => close.mutate()}
                >
                  {t.cash.closeSession}
                </Button>
              ) : null}
            </>
          }
        />

        <dl className="mb-4 grid grid-cols-2 gap-x-6 gap-y-1 text-sm sm:grid-cols-4">
          <dt className="text-[var(--text-secondary)]">{t.cash.openingFloat}</dt>
          <dd className="tabular text-right">{data.totals.opening_float}</dd>
          <dt className="text-[var(--text-secondary)]">{t.cash.receipts}</dt>
          <dd className="tabular text-right">{data.totals.receipts}</dd>
          <dt className="text-[var(--text-secondary)]">{t.cash.expenses}</dt>
          <dd className="tabular text-right">{data.totals.expenses}</dd>
          <dt className="text-[var(--text-secondary)]">{t.cash.deposits}</dt>
          <dd className="tabular text-right">{data.totals.deposits}</dd>
          <dt className="font-medium">{t.cash.expected}</dt>
          <dd className="tabular text-right font-medium">{data.totals.expected}</dd>
          {data.totals.counted !== null ? (
            <>
              <dt className="font-medium">{t.cash.countedAmount}</dt>
              <dd className="tabular text-right font-medium">
                {data.totals.counted}
              </dd>
            </>
          ) : null}
        </dl>

        {data.discrepancy !== null && data.discrepancy !== '0.00' ? (
          <div className="mb-4">
            <Alert tone="danger">
              {Number(data.discrepancy) < 0 ? t.cash.shortage : t.cash.overage}:{' '}
              {data.discrepancy}
              {data.discrepancy_reason ? ` — ${data.discrepancy_reason}` : ''}
            </Alert>
          </div>
        ) : data.discrepancy === '0.00' ? (
          <div className="mb-4">
            <Alert tone="success">{t.cash.balanced}</Alert>
          </div>
        ) : null}

        {data.movements.length === 0 ? (
          <EmptyState title={t.cash.movements} />
        ) : (
          <Table>
            <thead>
              <tr>
                <Th>{t.cash.occurredAt}</Th>
                <Th>{t.cash.kind}</Th>
                <Th>{t.cash.description}</Th>
                <Th numeric>{t.cash.amount}</Th>
              </tr>
            </thead>
            <tbody>
              {data.movements.map((movement) => (
                <tr key={movement.id}>
                  <Td>{formatDateTime(movement.occurred_at)}</Td>
                  <Td>{kindLabel[movement.kind]}</Td>
                  <Td>{movement.description ?? '—'}</Td>
                  <Td numeric>{movement.amount}</Td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </Card>

      <TakePaymentDialog
        open={paymentOpen}
        sessionId={sessionId}
        businessDate={data.business_date}
        onClose={() => setPaymentOpen(false)}
        onDone={(message) => {
          setPaymentOpen(false)
          onChanged(message)
        }}
        onError={(message) => {
          setPaymentOpen(false)
          onError(message)
        }}
      />
      <AmountDialog
        open={expenseOpen}
        title={t.cash.recordExpense}
        label={t.cash.description}
        requireText
        onClose={() => setExpenseOpen(false)}
        onSubmit={(amount, description) =>
          api.post<CashSessionDetail>(`/cash/sessions/${sessionId}/expenses`, {
            amount,
            description,
          })
        }
        onDone={() => {
          setExpenseOpen(false)
          onChanged(t.cash.expenseRecorded)
        }}
        onError={(message) => {
          setExpenseOpen(false)
          onError(message)
        }}
      />
      <DepositDialog
        open={depositOpen}
        sessionId={sessionId}
        onClose={() => setDepositOpen(false)}
        onDone={() => {
          setDepositOpen(false)
          onChanged(t.cash.depositRecorded)
        }}
        onError={(message) => {
          setDepositOpen(false)
          onError(message)
        }}
      />
      <CountDialog
        open={countOpen}
        sessionId={sessionId}
        expected={data.totals.expected}
        onClose={() => setCountOpen(false)}
        onDone={() => {
          setCountOpen(false)
          onChanged(t.cash.countSubmitted)
        }}
        onError={(message) => {
          setCountOpen(false)
          onError(message)
        }}
      />
    </>
  )
}

function CreateRegisterDialog({
  open,
  onClose,
  onDone,
  onError,
}: {
  open: boolean
  onClose: () => void
  onDone: () => void
  onError: (message: string) => void
}) {
  const { t } = useI18n()
  const [code, setCode] = useState('TILL-1')
  const [name, setName] = useState('')

  const create = useMutation({
    mutationFn: () =>
      api.post<CashRegister>('/cash/registers', {
        code,
        name,
        account_code: '5010',
      }),
    onSuccess: onDone,
    onError: (err) => onError(describeError(err, t)),
  })

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={t.cash.newRegister}
      closeLabel={t.common.close}
      footer={
        <>
          <Button onClick={onClose} disabled={create.isPending}>
            {t.common.cancel}
          </Button>
          <Button
            variant="primary"
            loading={create.isPending}
            disabled={!code || !name}
            onClick={() => create.mutate()}
          >
            {t.common.create}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <Field label={t.sales.customerCode} htmlFor="reg-code" required>
          <Input
            id="reg-code"
            value={code}
            onChange={(event) => setCode(event.target.value)}
          />
        </Field>
        <Field label={t.sales.customerName} htmlFor="reg-name" required>
          <Input
            id="reg-name"
            value={name}
            onChange={(event) => setName(event.target.value)}
          />
        </Field>
      </div>
    </Dialog>
  )
}

function OpenSessionDialog({
  open,
  registers,
  onClose,
  onDone,
  onError,
}: {
  open: boolean
  registers: CashRegister[]
  onClose: () => void
  onDone: (session: CashSessionDetail) => void
  onError: (message: string) => void
}) {
  const { t } = useI18n()
  const [registerId, setRegisterId] = useState('')
  const [businessDate, setBusinessDate] = useState(() =>
    new Date().toISOString().slice(0, 10),
  )
  const [openingFloat, setOpeningFloat] = useState('0')

  const act = useMutation({
    mutationFn: () =>
      api.post<CashSessionDetail>('/cash/sessions', {
        register_id: registerId || registers[0]?.id,
        business_date: businessDate,
        opening_float: openingFloat || '0',
      }),
    onSuccess: onDone,
    onError: (err) => onError(describeError(err, t)),
  })

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={t.cash.openSession}
      closeLabel={t.common.close}
      footer={
        <>
          <Button onClick={onClose} disabled={act.isPending}>
            {t.common.cancel}
          </Button>
          <Button variant="primary" loading={act.isPending} onClick={() => act.mutate()}>
            {t.cash.openSession}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <Field label={t.cash.register} htmlFor="sess-register" required>
          <Select
            id="sess-register"
            value={registerId || registers[0]?.id || ''}
            onChange={(event) => setRegisterId(event.target.value)}
          >
            {registers.map((register) => (
              <option key={register.id} value={register.id}>
                {register.code} · {register.name}
              </option>
            ))}
          </Select>
        </Field>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label={t.cash.businessDate} htmlFor="sess-date" required>
            <Input
              id="sess-date"
              type="date"
              value={businessDate}
              onChange={(event) => setBusinessDate(event.target.value)}
            />
          </Field>
          <Field label={t.cash.openingFloat} htmlFor="sess-float">
            <Input
              id="sess-float"
              inputMode="decimal"
              className="tabular text-right"
              value={openingFloat}
              onChange={(event) => setOpeningFloat(event.target.value)}
            />
          </Field>
        </div>
      </div>
    </Dialog>
  )
}

function TakePaymentDialog({
  open,
  sessionId,
  businessDate,
  onClose,
  onDone,
  onError,
}: {
  open: boolean
  sessionId: string
  businessDate: string
  onClose: () => void
  onDone: (message: string) => void
  onError: (message: string) => void
}) {
  const { t } = useI18n()
  const customers = useCustomers(open)
  const [customerId, setCustomerId] = useState('')
  const [amount, setAmount] = useState('')
  const [autoAllocate, setAutoAllocate] = useState(true)

  const act = useMutation({
    mutationFn: () =>
      api.post<{ number: string; advance: string }>(
        `/cash/sessions/${sessionId}/payments`,
        {
          customer_id: customerId,
          amount,
          received_on: businessDate,
          auto_allocate: autoAllocate,
        },
      ),
    onSuccess: (payment) => {
      setAmount('')
      onDone(
        payment.advance !== '0.00'
          ? `${t.sales.paymentRecorded} ${payment.number} — ${t.sales.advance}: ${payment.advance}`
          : `${t.sales.paymentRecorded} ${payment.number}`,
      )
    },
    onError: (err) => onError(describeError(err, t)),
  })

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={t.cash.takePayment}
      closeLabel={t.common.close}
      footer={
        <>
          <Button onClick={onClose} disabled={act.isPending}>
            {t.common.cancel}
          </Button>
          <Button
            variant="primary"
            loading={act.isPending}
            disabled={!customerId || !amount}
            onClick={() => act.mutate()}
          >
            {t.common.save}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <Field label={t.sales.customer} htmlFor="cash-pay-customer" required>
          <Select
            id="cash-pay-customer"
            value={customerId}
            onChange={(event) => setCustomerId(event.target.value)}
          >
            <option value="">—</option>
            {(customers.data ?? []).map((customer) => (
              <option key={customer.id} value={customer.id}>
                {customer.display_name}
              </option>
            ))}
          </Select>
        </Field>
        <Field label={t.cash.amount} htmlFor="cash-pay-amount" required>
          <Input
            id="cash-pay-amount"
            inputMode="decimal"
            className="tabular text-right"
            value={amount}
            onChange={(event) => setAmount(event.target.value)}
          />
        </Field>
        <Checkbox
          label={t.sales.autoAllocate}
          description={t.sales.advanceHint}
          checked={autoAllocate}
          onChange={(event) => setAutoAllocate(event.target.checked)}
        />
      </div>
    </Dialog>
  )
}

function AmountDialog({
  open,
  title,
  label,
  requireText,
  onClose,
  onSubmit,
  onDone,
  onError,
}: {
  open: boolean
  title: string
  label: string
  requireText?: boolean
  onClose: () => void
  onSubmit: (amount: string, description: string) => Promise<unknown>
  onDone: () => void
  onError: (message: string) => void
}) {
  const { t } = useI18n()
  const [amount, setAmount] = useState('')
  const [description, setDescription] = useState('')

  const act = useMutation({
    mutationFn: () => onSubmit(amount, description),
    onSuccess: () => {
      setAmount('')
      setDescription('')
      onDone()
    },
    onError: (err) => onError(describeError(err, t)),
  })

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={title}
      closeLabel={t.common.close}
      footer={
        <>
          <Button onClick={onClose} disabled={act.isPending}>
            {t.common.cancel}
          </Button>
          <Button
            variant="primary"
            loading={act.isPending}
            disabled={!amount || (requireText && !description.trim())}
            onClick={() => act.mutate()}
          >
            {t.common.save}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <Field label={t.cash.amount} htmlFor="amt-amount" required>
          <Input
            id="amt-amount"
            inputMode="decimal"
            className="tabular text-right"
            value={amount}
            onChange={(event) => setAmount(event.target.value)}
          />
        </Field>
        <Field label={label} htmlFor="amt-description" required={requireText}>
          <Input
            id="amt-description"
            value={description}
            onChange={(event) => setDescription(event.target.value)}
          />
        </Field>
      </div>
    </Dialog>
  )
}

function DepositDialog({
  open,
  sessionId,
  onClose,
  onDone,
  onError,
}: {
  open: boolean
  sessionId: string
  onClose: () => void
  onDone: () => void
  onError: (message: string) => void
}) {
  const { t } = useI18n()
  const [amount, setAmount] = useState('')
  const [route, setRoute] = useState<DepositRoute>('STAFF')
  const [reference, setReference] = useState('')

  const act = useMutation({
    mutationFn: () =>
      api.post<CashSessionDetail>(`/cash/sessions/${sessionId}/deposits`, {
        amount,
        route,
        reference: reference || null,
      }),
    onSuccess: () => {
      setAmount('')
      setReference('')
      onDone()
    },
    onError: (err) => onError(describeError(err, t)),
  })

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={t.cash.recordDeposit}
      closeLabel={t.common.close}
      footer={
        <>
          <Button onClick={onClose} disabled={act.isPending}>
            {t.common.cancel}
          </Button>
          <Button
            variant="primary"
            loading={act.isPending}
            disabled={!amount}
            onClick={() => act.mutate()}
          >
            {t.common.save}
          </Button>
        </>
      }
    >
      <p className="mb-4 text-sm text-[var(--text-secondary)]">
        {t.cash.depositHint}
      </p>
      <div className="flex flex-col gap-4">
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label={t.cash.amount} htmlFor="dep-amount" required>
            <Input
              id="dep-amount"
              inputMode="decimal"
              className="tabular text-right"
              value={amount}
              onChange={(event) => setAmount(event.target.value)}
            />
          </Field>
          <Field label={t.cash.route} htmlFor="dep-route" required>
            <Select
              id="dep-route"
              value={route}
              onChange={(event) => setRoute(event.target.value as DepositRoute)}
            >
              <option value="STAFF">{t.cash.routeStaff}</option>
              <option value="COLLECTION">{t.cash.routeCollection}</option>
            </Select>
          </Field>
        </div>
        <Field label={t.cash.reference} htmlFor="dep-reference">
          <Input
            id="dep-reference"
            value={reference}
            onChange={(event) => setReference(event.target.value)}
          />
        </Field>
      </div>
    </Dialog>
  )
}

function CountDialog({
  open,
  sessionId,
  expected,
  onClose,
  onDone,
  onError,
}: {
  open: boolean
  sessionId: string
  expected: string
  onClose: () => void
  onDone: () => void
  onError: (message: string) => void
}) {
  const { t } = useI18n()
  const [counted, setCounted] = useState('')
  const [reason, setReason] = useState('')

  // Shown while typing so the cashier sees the difference before submitting,
  // rather than discovering it in a refusal.
  const difference =
    counted.trim() && /^\d+(\.\d+)?$/.test(counted.trim())
      ? (Number(counted) - Number(expected)).toFixed(2)
      : null
  const differs = difference !== null && difference !== '0.00'

  const act = useMutation({
    mutationFn: () =>
      api.post<CashSessionDetail>(`/cash/sessions/${sessionId}/count`, {
        counted_amount: counted,
        reason: reason || null,
      }),
    onSuccess: () => {
      setCounted('')
      setReason('')
      onDone()
    },
    onError: (err) => onError(describeError(err, t)),
  })

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={t.cash.submitCount}
      closeLabel={t.common.close}
      footer={
        <>
          <Button onClick={onClose} disabled={act.isPending}>
            {t.common.cancel}
          </Button>
          <Button
            variant="primary"
            loading={act.isPending}
            disabled={!counted || (differs && !reason.trim())}
            onClick={() => act.mutate()}
          >
            {t.common.save}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <dl className="grid grid-cols-2 gap-1 text-sm">
          <dt className="text-[var(--text-secondary)]">{t.cash.expected}</dt>
          <dd className="tabular text-right font-medium">{expected}</dd>
        </dl>
        <Field label={t.cash.countedAmount} htmlFor="count-amount" required>
          <Input
            id="count-amount"
            inputMode="decimal"
            className="tabular text-right"
            value={counted}
            onChange={(event) => setCounted(event.target.value)}
          />
        </Field>
        {differs ? (
          <>
            <Alert tone="warning">
              {Number(difference) < 0 ? t.cash.shortage : t.cash.overage}:{' '}
              {difference} — {t.cash.discrepancyHint}
            </Alert>
            <Field
              label={t.cash.discrepancyReason}
              htmlFor="count-reason"
              required
            >
              <Input
                id="count-reason"
                value={reason}
                onChange={(event) => setReason(event.target.value)}
              />
            </Field>
          </>
        ) : null}
      </div>
    </Dialog>
  )
}
