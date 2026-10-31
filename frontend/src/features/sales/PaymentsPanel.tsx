import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useI18n } from '@/i18n'
import { useAuth } from '@/features/auth/AuthProvider'
import { api } from '@/lib/api'
import { useCustomers, useInvoices, usePayments } from '@/lib/queries'
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
  Select,
  Table,
  Td,
  Th,
} from '@/components/ui'
import type { Payment, PaymentDetail, PaymentMethod } from '@/lib/types'

export function PaymentsPanel() {
  const { t, formatDate } = useI18n()
  const { can } = useAuth()
  const queryClient = useQueryClient()

  const mayRead = can('sales.invoice.read')
  const mayRecord = can('sales.payment.record')
  const mayRefund = can('sales.refund.approve')

  const payments = usePayments(mayRead)
  const [createOpen, setCreateOpen] = useState(false)
  const [refundTarget, setRefundTarget] = useState<Payment | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const invalidate = () => void queryClient.invalidateQueries({ queryKey: ['sales'] })

  if (!mayRead) return <Alert tone="warning">{t.moduleStatus.noAccess}</Alert>
  if (payments.isLoading) return <LoadingState label={t.common.loading} />
  if (payments.isError) {
    return (
      <ErrorState
        title={t.errors.loadFailedTitle}
        message={describeError(payments.error, t)}
        requestId={errorRequestId(payments.error)}
        requestIdLabel={t.common.requestId}
        onRetry={() => void payments.refetch()}
        retryLabel={t.common.retry}
      />
    )
  }

  const rows = payments.data ?? []
  const methodLabel: Record<PaymentMethod, string> = {
    CASH: t.sales.cash,
    CARD: t.sales.card,
    BANK_TRANSFER: t.sales.bankTransfer,
  }

  return (
    <>
      <Card>
        <CardHeader
          title={t.sales.paymentsTab}
          description={t.sales.advanceHint}
          actions={
            mayRecord ? (
              <Button variant="primary" size="sm" onClick={() => setCreateOpen(true)}>
                {t.sales.newPayment}
              </Button>
            ) : undefined
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

        {rows.length === 0 ? (
          <EmptyState title={t.sales.emptyPayments} />
        ) : (
          <Table>
            <thead>
              <tr>
                <Th>{t.sales.paymentNumber}</Th>
                <Th>{t.sales.receivedOn}</Th>
                <Th>{t.sales.method}</Th>
                <Th numeric>{t.sales.amount}</Th>
                <Th numeric>{t.sales.allocated}</Th>
                <Th numeric>{t.sales.advance}</Th>
                <Th>{t.sales.status}</Th>
                <Th>{t.common.actions}</Th>
              </tr>
            </thead>
            <tbody>
              {rows.map((payment) => (
                <tr key={payment.id}>
                  <Td>
                    <span className="font-mono text-xs">{payment.number}</span>
                  </Td>
                  <Td>{formatDate(payment.received_on)}</Td>
                  <Td>{methodLabel[payment.method]}</Td>
                  <Td numeric>{payment.amount}</Td>
                  <Td numeric>{payment.allocated}</Td>
                  <Td numeric>
                    {/* An advance is a liability. Marked, not just shown. */}
                    {payment.advance !== '0.00' ? (
                      <span style={{ color: 'var(--warning)' }}>{payment.advance}</span>
                    ) : (
                      ''
                    )}
                  </Td>
                  <Td>
                    {payment.status === 'REFUNDED' ? (
                      <Badge tone="danger">{t.sales.refunded}</Badge>
                    ) : (
                      <Badge tone="success">{t.sales.recorded}</Badge>
                    )}
                  </Td>
                  <Td>
                    {payment.status === 'RECORDED' && mayRefund ? (
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => {
                          setError(null)
                          setRefundTarget(payment)
                        }}
                      >
                        {t.sales.refund}
                      </Button>
                    ) : null}
                  </Td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </Card>

      <RecordPaymentDialog
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onRecorded={(message) => {
          setCreateOpen(false)
          setError(null)
          setNotice(message)
          invalidate()
        }}
      />

      <RefundDialog
        payment={refundTarget}
        onClose={() => setRefundTarget(null)}
        onDone={(message) => {
          setRefundTarget(null)
          setNotice(message)
          invalidate()
        }}
        onError={(message) => {
          setRefundTarget(null)
          setError(message)
        }}
      />
    </>
  )
}

function RecordPaymentDialog({
  open,
  onClose,
  onRecorded,
}: {
  open: boolean
  onClose: () => void
  onRecorded: (message: string) => void
}) {
  const { t } = useI18n()
  const customers = useCustomers(open)
  const [customerId, setCustomerId] = useState('')
  const [amount, setAmount] = useState('')
  const [method, setMethod] = useState<PaymentMethod>('CASH')
  const [receivedOn, setReceivedOn] = useState(() =>
    new Date().toISOString().slice(0, 10),
  )
  const [autoAllocate, setAutoAllocate] = useState(true)
  const [reference, setReference] = useState('')
  const [formError, setFormError] = useState<string | null>(null)

  // Only this customer's open invoices are relevant to the allocation hint.
  const openInvoices = useInvoices(open && Boolean(customerId), {
    customer_id: customerId || undefined,
  })
  const outstanding = (openInvoices.data ?? []).filter(
    (invoice) => invoice.outstanding !== '0.00' && invoice.status !== 'VOID',
  )

  const record = useMutation({
    mutationFn: () =>
      api.post<PaymentDetail>('/sales/payments', {
        customer_id: customerId,
        amount,
        method,
        received_on: receivedOn,
        reference: reference || null,
        auto_allocate: autoAllocate,
      }),
    onSuccess: (payment) => {
      setAmount('')
      setReference('')
      setFormError(null)
      const note =
        payment.advance !== '0.00'
          ? `${t.sales.paymentRecorded} ${payment.number} — ${t.sales.advance}: ${payment.advance}`
          : `${t.sales.paymentRecorded} ${payment.number}`
      onRecorded(note)
    },
    onError: (err) => setFormError(describeError(err, t)),
  })

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={t.sales.newPayment}
      closeLabel={t.common.close}
      footer={
        <>
          <Button onClick={onClose} disabled={record.isPending}>
            {t.common.cancel}
          </Button>
          <Button
            variant="primary"
            loading={record.isPending}
            disabled={!customerId || !amount}
            onClick={() => {
              setFormError(null)
              record.mutate()
            }}
          >
            {t.common.save}
          </Button>
        </>
      }
    >
      {formError ? (
        <div className="mb-4">
          <Alert tone="danger">{formError}</Alert>
        </div>
      ) : null}

      <div className="flex flex-col gap-4">
        <Field label={t.sales.customer} htmlFor="pay-customer" required>
          <Select
            id="pay-customer"
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

        <div className="grid gap-4 sm:grid-cols-3">
          <Field label={t.sales.amount} htmlFor="pay-amount" required>
            <Input
              id="pay-amount"
              inputMode="decimal"
              className="tabular text-right"
              value={amount}
              onChange={(event) => setAmount(event.target.value)}
            />
          </Field>
          <Field label={t.sales.method} htmlFor="pay-method" required>
            <Select
              id="pay-method"
              value={method}
              onChange={(event) => setMethod(event.target.value as PaymentMethod)}
            >
              <option value="CASH">{t.sales.cash}</option>
              <option value="CARD">{t.sales.card}</option>
              <option value="BANK_TRANSFER">{t.sales.bankTransfer}</option>
            </Select>
          </Field>
          <Field label={t.sales.receivedOn} htmlFor="pay-date" required>
            <Input
              id="pay-date"
              type="date"
              value={receivedOn}
              onChange={(event) => setReceivedOn(event.target.value)}
            />
          </Field>
        </div>

        <Field label={t.sales.reference} htmlFor="pay-reference">
          <Input
            id="pay-reference"
            value={reference}
            onChange={(event) => setReference(event.target.value)}
          />
        </Field>

        <Checkbox
          label={t.sales.autoAllocate}
          description={t.sales.advanceHint}
          checked={autoAllocate}
          onChange={(event) => setAutoAllocate(event.target.checked)}
        />

        {customerId && outstanding.length > 0 ? (
          <div className="rounded-lg border border-[var(--border-subtle)] p-3">
            <p className="mb-2 text-xs font-semibold text-[var(--text-secondary)]">
              {t.sales.allocateTo}
            </p>
            <ul className="flex flex-col gap-1 text-xs">
              {outstanding.map((invoice) => (
                <li key={invoice.id} className="flex justify-between gap-3">
                  <span className="font-mono">{invoice.number}</span>
                  <span className="tabular">{invoice.outstanding}</span>
                </li>
              ))}
            </ul>
          </div>
        ) : null}
      </div>
    </Dialog>
  )
}

function RefundDialog({
  payment,
  onClose,
  onDone,
  onError,
}: {
  payment: Payment | null
  onClose: () => void
  onDone: (message: string) => void
  onError: (message: string) => void
}) {
  const { t } = useI18n()
  const [reason, setReason] = useState('')

  const act = useMutation({
    mutationFn: () =>
      api.post<PaymentDetail>(`/sales/payments/${payment?.id}/refund`, { reason }),
    onSuccess: () => {
      setReason('')
      onDone(t.sales.refunded2)
    },
    onError: (err) => onError(describeError(err, t)),
  })

  return (
    <Dialog
      open={payment !== null}
      onClose={onClose}
      title={`${t.sales.refundTitle}: ${payment?.number ?? ''}`}
      closeLabel={t.common.close}
      footer={
        <>
          <Button onClick={onClose} disabled={act.isPending}>
            {t.common.cancel}
          </Button>
          <Button
            variant="danger"
            loading={act.isPending}
            disabled={!reason.trim()}
            onClick={() => act.mutate()}
          >
            {t.sales.refund}
          </Button>
        </>
      }
    >
      <p className="mb-4 text-sm text-[var(--text-secondary)]">{t.sales.refundBody}</p>
      <Field label={t.sales.reason} htmlFor="refund-reason" required>
        <Input
          id="refund-reason"
          value={reason}
          onChange={(event) => setReason(event.target.value)}
        />
      </Field>
    </Dialog>
  )
}
