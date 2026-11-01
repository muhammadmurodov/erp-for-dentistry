import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useI18n } from '@/i18n'
import { useAuth } from '@/features/auth/AuthProvider'
import { api } from '@/lib/api'
import { useSettlement, useSettlementHistory } from '@/lib/queries'
import { describeError, errorRequestId } from '@/lib/errorMessage'
import { computeRemaining, formatAmount } from '@/lib/money'
import {
  Alert,
  Badge,
  Button,
  Card,
  CardHeader,
  EmptyState,
  ErrorState,
  Field,
  Input,
  LoadingState,
  Table,
  Td,
  Th,
} from '@/components/ui'
import type { SettlementResult, SettlementStatus } from '@/lib/types'

/**
 * Taking a payment against one bill.
 *
 * The rule this panel exists to honour: **the remaining balance is shown,
 * never typed.** It is rendered into a disabled input, and there is no state
 * holding an editable copy of it — the figure is recomputed from the invoice
 * total, the confirmed payments and whatever the cashier has typed, on every
 * keystroke, in exact decimal arithmetic (`lib/money.ts`, never `number`).
 *
 * Everything the panel disables, the backend refuses independently: an
 * overpayment, a payment on a settled bill, cash with no drawer open, a
 * resubmitted form. The disabling is here so a cashier is not invited to do
 * something that will fail; it is not what makes it fail.
 */

const STATUS_TONE: Record<SettlementStatus, 'neutral' | 'success' | 'warning' | 'danger'> =
  {
    UNPAID: 'warning',
    PARTIALLY_PAID: 'warning',
    FULLY_PAID: 'success',
    WRITTEN_OFF: 'danger',
    NOT_PAYABLE: 'neutral',
  }

function newRequestKey(): string {
  // Stable for the life of one submission, so a retry after a timeout is the
  // same submission and the backend replays its outcome instead of charging
  // the patient twice.
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) {
    return crypto.randomUUID()
  }
  return `k-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`
}

export function PaymentPanel({
  invoiceId,
  onSettled,
}: {
  invoiceId: string
  onSettled?: () => void
}) {
  const { t, formatDate, formatMoney } = useI18n()
  const { can } = useAuth()
  const queryClient = useQueryClient()

  const mayTake = can('sales.payment.record')
  const settlement = useSettlement(invoiceId)
  const history = useSettlementHistory(invoiceId)

  const [cash, setCash] = useState('')
  const [card, setCard] = useState('')
  const [advances, setAdvances] = useState('')
  const [reference, setReference] = useState('')
  const [requestKey, setRequestKey] = useState(newRequestKey)
  const [notice, setNotice] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  // A different bill is a different form. Without this, amounts typed for one
  // invoice would still be sitting in the fields when another is opened.
  useEffect(() => {
    setCash('')
    setCard('')
    setAdvances('')
    setReference('')
    setRequestKey(newRequestKey())
    setNotice(null)
    setError(null)
  }, [invoiceId])

  const view = settlement.data
  const draft = useMemo(
    () =>
      computeRemaining({
        total: view?.treatment_total ?? '0',
        paid: view?.paid ?? '0',
        cash,
        card,
        advances,
        advancesAvailable: view?.advances_available ?? '0',
      }),
    [view, cash, card, advances],
  )

  const settle = useMutation({
    mutationFn: () =>
      api.post<SettlementResult>(`/clinic/invoices/${invoiceId}/settlement`, {
        cash: cash.trim() === '' ? '0' : cash.trim().replace(',', '.'),
        card: card.trim() === '' ? '0' : card.trim().replace(',', '.'),
        advances:
          advances.trim() === '' ? '0' : advances.trim().replace(',', '.'),
        card_reference: reference.trim() === '' ? null : reference.trim(),
        cash_session_id: view?.open_cash_session_id ?? null,
        request_key: requestKey,
      }),
    onSuccess: (result) => {
      setError(null)
      setNotice(result.replayed ? t.clinic.replayNotice : t.clinic.paymentTaken)
      setCash('')
      setCard('')
      setAdvances('')
      setReference('')
      // A fresh key: the next payment is a new submission, not a retry.
      setRequestKey(newRequestKey())
      void queryClient.invalidateQueries({ queryKey: ['clinic'] })
      void queryClient.invalidateQueries({ queryKey: ['sales'] })
      void queryClient.invalidateQueries({ queryKey: ['cash'] })
      onSettled?.()
    },
    onError: (cause) => {
      setNotice(null)
      setError(describeError(cause, t))
    },
  })

  if (settlement.isLoading) return <LoadingState label={t.common.loading} />
  if (settlement.isError || !view) {
    return (
      <ErrorState
        title={t.errors.loadFailedTitle}
        message={describeError(settlement.error, t)}
        requestId={errorRequestId(settlement.error)}
        requestIdLabel={t.common.requestId}
        onRetry={() => void settlement.refetch()}
        retryLabel={t.common.retry}
      />
    )
  }

  const statusLabel: Record<SettlementStatus, string> = {
    UNPAID: t.clinic.statusUnpaid,
    PARTIALLY_PAID: t.clinic.statusPartial,
    FULLY_PAID: t.clinic.statusFull,
    WRITTEN_OFF: t.clinic.statusWrittenOff,
    NOT_PAYABLE: t.clinic.statusNotPayable,
  }

  const settled = view.status === 'FULLY_PAID'
  const open = view.accepts_payment
  const drawerOpen = view.open_cash_session_id !== null
  // Rules 4 and 5: once the fields on the form settle the bill, the ones that
  // are still empty have nothing left to take, so they are disabled. Rule 6:
  // clearing an amount puts the balance back and re-enables them, because
  // this is derived from the draft rather than latched.
  const nothingLeft = open && draft.remaining === 0n && !draft.invalid
  const disableIfSpent = (value: string) => nothingLeft && value.trim() === ''

  const blocked =
    !open ||
    draft.invalid ||
    draft.overpays ||
    draft.advanceExceedsAvailable ||
    draft.draftTotal === 0n ||
    (!drawerOpen && cash.trim() !== '')

  const problem = !open
    ? null
    : draft.invalid
      ? t.clinic.invalidAmount
      : draft.overpays
        ? t.clinic.overpayWarning
        : draft.advanceExceedsAvailable
          ? t.clinic.advanceTooMuch
          : null

  return (
    <div className="flex flex-col gap-4">
      <Card>
        <CardHeader
          title={`${t.clinic.paymentHeading} · ${view.invoice_number}`}
          actions={<Badge tone={STATUS_TONE[view.status]}>{statusLabel[view.status]}</Badge>}
        />

        <dl className="mb-4 grid gap-x-6 gap-y-2 sm:grid-cols-2">
          <Figure label={t.clinic.treatmentPrice} value={formatMoney(view.treatment_total, view.currency)} strong />
          <Figure label={t.clinic.previouslyPaid} value={formatMoney(view.paid, view.currency)} />
          <Figure label={t.clinic.paidCash} value={formatMoney(view.paid_cash, view.currency)} muted />
          <Figure label={t.clinic.paidCard} value={formatMoney(view.paid_card, view.currency)} muted />
          <Figure
            label={t.clinic.advancesAvailable}
            value={formatMoney(view.advances_available, view.currency)}
          />
          {view.status === 'WRITTEN_OFF' ? (
            <Figure label={t.clinic.writtenOff} value={formatMoney(view.written_off, view.currency)} />
          ) : null}
        </dl>

        {settled ? (
          <div className="mb-4">
            <Alert tone="success">{t.clinic.fullyPaidNotice}</Alert>
          </div>
        ) : null}
        {!open && !settled ? (
          <div className="mb-4">
            <Alert tone="info">{t.clinic.notPayableNotice}</Alert>
          </div>
        ) : null}
        {/*
          Said before the form rather than under it: somebody who may not take
          a payment should not fill one in first and find out afterwards.
        */}
        {!mayTake ? (
          <div className="mb-4">
            <Alert tone="warning">{t.moduleStatus.noAccess}</Alert>
          </div>
        ) : null}
        {mayTake && open && !drawerOpen ? (
          <div className="mb-4">
            <Alert tone="warning">{t.clinic.noDrawerOpen}</Alert>
          </div>
        ) : null}
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

        <form
          className="flex flex-col gap-4"
          onSubmit={(event) => {
            event.preventDefault()
            if (!blocked && mayTake) settle.mutate()
          }}
        >
          <div className="grid gap-4 sm:grid-cols-3">
            {/* The drawer is explained once, in the alert above, not twice. */}
            <Field label={t.clinic.cashAmount} htmlFor="settle-cash">
              <Input
                id="settle-cash"
                inputMode="decimal"
                autoComplete="off"
                placeholder="0"
                value={cash}
                disabled={
                  !mayTake || !open || !drawerOpen || disableIfSpent(cash) || settle.isPending
                }
                onChange={(event) => setCash(event.target.value)}
              />
            </Field>
            <Field label={t.clinic.cardAmount} htmlFor="settle-card">
              <Input
                id="settle-card"
                inputMode="decimal"
                autoComplete="off"
                placeholder="0"
                value={card}
                disabled={!mayTake || !open || disableIfSpent(card) || settle.isPending}
                onChange={(event) => setCard(event.target.value)}
              />
            </Field>
            <Field label={t.clinic.applyAdvance} htmlFor="settle-advance">
              <Input
                id="settle-advance"
                inputMode="decimal"
                autoComplete="off"
                placeholder="0"
                value={advances}
                disabled={
                  !mayTake ||
                  !open ||
                  view.advances_available === '0.00' ||
                  disableIfSpent(advances) ||
                  settle.isPending
                }
                onChange={(event) => setAdvances(event.target.value)}
              />
            </Field>
          </div>

          <div className="grid gap-4 sm:grid-cols-2">
            <Field label={t.clinic.cardReference} htmlFor="settle-reference">
              <Input
                id="settle-reference"
                autoComplete="off"
                value={reference}
                disabled={!mayTake || !open || card.trim() === '' || settle.isPending}
                onChange={(event) => setReference(event.target.value)}
              />
            </Field>
            {/*
              The remaining balance. Disabled and never bound to a setter:
              there is no state it could be edited into, and the request has
              no field to carry one if there were.
            */}
            <Field
              label={t.clinic.remaining}
              htmlFor="settle-remaining"
              hint={t.clinic.remainingHint}
              error={problem ?? undefined}
            >
              <Input
                id="settle-remaining"
                readOnly
                disabled
                aria-live="polite"
                className="font-semibold"
                value={formatMoney(
                  formatAmount(
                    // Over the balance, the figure is what the form is over
                    // by, not nothing. `remaining` is clamped at zero because
                    // a balance owed cannot be negative, and showing that
                    // clamp here would tell a cashier the bill is covered at
                    // the exact moment it is not.
                    draft.overpays
                      ? draft.remainingBeforeDraft - draft.draftTotal
                      : draft.remaining,
                  ),
                  view.currency,
                )}
              />
            </Field>
          </div>

          {/* The refusal is stated above the form, not after it. */}
          {mayTake ? (
            <div className="flex flex-wrap items-center gap-2">
              <Button
                type="submit"
                variant="primary"
                disabled={blocked}
                loading={settle.isPending}
              >
                {settle.isPending ? t.clinic.takingPayment : t.clinic.confirmPayment}
              </Button>
              <Button
                type="button"
                variant="ghost"
                disabled={settle.isPending}
                onClick={() => {
                  setCash('')
                  setCard('')
                  setAdvances('')
                  setReference('')
                }}
              >
                {t.common.clear}
              </Button>
              {open && draft.draftTotal === 0n && !draft.invalid ? (
                <span className="text-xs text-[var(--text-muted)]">
                  {t.clinic.nothingEntered}
                </span>
              ) : null}
            </div>
          ) : null}
        </form>
      </Card>

      <Card>
        <CardHeader title={t.clinic.historyHeading} />
        {(history.data ?? []).length === 0 ? (
          <EmptyState title={t.clinic.emptyHistory} />
        ) : (
          <Table>
            <thead>
              <tr>
                <Th>{t.sales.paymentNumber}</Th>
                <Th>{t.clinic.issueDate}</Th>
                <Th>{t.clinic.method}</Th>
                <Th numeric>{t.sales.amount}</Th>
                <Th>{t.sales.status}</Th>
              </tr>
            </thead>
            <tbody>
              {(history.data ?? []).map((row) => (
                <tr key={row.allocation_id}>
                  <Td>
                    <span className="font-mono text-xs">{row.payment_number}</span>
                  </Td>
                  <Td>{formatDate(row.received_on)}</Td>
                  <Td>
                    {row.method === 'CASH'
                      ? t.clinic.cash
                      : row.method === 'CARD'
                        ? t.clinic.card
                        : t.clinic.bankTransfer}
                  </Td>
                  <Td numeric>{formatMoney(row.amount, view.currency)}</Td>
                  <Td>
                    {/* A reversal is shown, not hidden: the trail is the point. */}
                    {row.reversed_at ? (
                      <span title={row.reversal_reason ?? undefined}>
                        <Badge tone="danger">{t.clinic.reversed}</Badge>
                      </span>
                    ) : (
                      <Badge tone="success">{t.sales.recorded}</Badge>
                    )}
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

function Figure({
  label,
  value,
  strong,
  muted,
}: {
  label: string
  value: string
  strong?: boolean
  muted?: boolean
}) {
  return (
    <div className="flex items-baseline justify-between gap-4 border-b border-[var(--border-subtle)] py-1 last:border-0">
      <dt
        className={
          muted
            ? 'text-xs text-[var(--text-muted)]'
            : 'text-sm text-[var(--text-secondary)]'
        }
      >
        {label}
      </dt>
      <dd
        className={
          strong
            ? 'tabular text-base font-semibold text-[var(--text-primary)]'
            : muted
              ? 'tabular text-xs text-[var(--text-muted)]'
              : 'tabular text-sm font-medium text-[var(--text-primary)]'
        }
      >
        {value}
      </dd>
    </div>
  )
}
