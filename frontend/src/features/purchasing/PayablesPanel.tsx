import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useI18n } from '@/i18n'
import { useAuth } from '@/features/auth/AuthProvider'
import { api } from '@/lib/api'
import { useBills, usePayablesAgeing, useSupplierPayments } from '@/lib/queries'
import { describeError, errorRequestId } from '@/lib/errorMessage'
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
import type { SupplierPayment } from '@/lib/types'
import { PaySupplierDialog } from './PaySupplierDialog'

/**
 * What the clinic owes, and the money it has paid out.
 *
 * The ageing table is derived from live allocations every time it is drawn —
 * there is no stored balance behind any figure on this screen, which is why
 * it cannot disagree with the bills it came from.
 */
export function PayablesPanel() {
  const { t, formatMoney, formatDate } = useI18n()
  const { can } = useAuth()
  const mayPay = can('purchasing.payment.record')

  const ageing = usePayablesAgeing(true)
  const payments = useSupplierPayments({}, true)
  const [applying, setApplying] = useState<SupplierPayment | null>(null)
  const [paying, setPaying] = useState(false)

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader
          title={t.purchasing.ageingHeading}
          description={t.purchasing.ageingHint}
        />
        {ageing.isLoading ? (
          <LoadingState label={t.common.loading} />
        ) : ageing.isError ? (
          <ErrorState
            title={t.errors.loadFailedTitle}
            message={describeError(ageing.error, t)}
            requestId={errorRequestId(ageing.error)}
            requestIdLabel={t.common.requestId}
            onRetry={() => void ageing.refetch()}
            retryLabel={t.common.retry}
          />
        ) : (ageing.data ?? []).length === 0 ? (
          <EmptyState title={t.purchasing.nothingOwed} />
        ) : (
          <Table>
            <thead>
              <tr>
                <Th>{t.purchasing.supplier}</Th>
                <Th numeric>{t.purchasing.current}</Th>
                <Th numeric>{t.purchasing.days1_30}</Th>
                <Th numeric>{t.purchasing.days31_60}</Th>
                <Th numeric>{t.purchasing.days61_90}</Th>
                <Th numeric>{t.purchasing.daysOver90}</Th>
                <Th numeric>{t.purchasing.total}</Th>
              </tr>
            </thead>
            <tbody>
              {(ageing.data ?? []).map((row) => (
                <tr key={row.supplier_id}>
                  <Td>{row.supplier_name}</Td>
                  <Td numeric>{formatMoney(row.current)}</Td>
                  <Td numeric>{formatMoney(row.days_1_30)}</Td>
                  <Td numeric>{formatMoney(row.days_31_60)}</Td>
                  <Td numeric>{formatMoney(row.days_61_90)}</Td>
                  <Td numeric>
                    {Number(row.days_over_90) === 0 ? (
                      formatMoney(row.days_over_90)
                    ) : (
                      <span style={{ color: 'var(--danger)' }}>
                        {formatMoney(row.days_over_90)}
                      </span>
                    )}
                  </Td>
                  <Td numeric>
                    <span className="font-medium">{formatMoney(row.total)}</span>
                  </Td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </Card>

      <Card>
        <CardHeader
          title={t.purchasing.paymentsHeading}
          actions={
            mayPay ? (
              <Button size="sm" variant="primary" onClick={() => setPaying(true)}>
                {t.purchasing.pay}
              </Button>
            ) : undefined
          }
        />
        {payments.isLoading ? (
          <LoadingState label={t.common.loading} />
        ) : (payments.data ?? []).length === 0 ? (
          <EmptyState title={t.purchasing.noPayments} />
        ) : (
          <Table>
            <thead>
              <tr>
                <Th>{t.purchasing.reference}</Th>
                <Th>{t.purchasing.supplier}</Th>
                <Th>{t.purchasing.paidOn}</Th>
                <Th>{t.purchasing.method}</Th>
                <Th numeric>{t.purchasing.amount}</Th>
                <Th numeric>{t.purchasing.unapplied}</Th>
                <Th>{t.common.actions}</Th>
              </tr>
            </thead>
            <tbody>
              {(payments.data ?? []).map((payment) => (
                <tr key={payment.id}>
                  <Td>
                    <span className="font-mono text-xs">{payment.code}</span>
                  </Td>
                  <Td>{payment.supplier_name}</Td>
                  <Td>{formatDate(payment.paid_on)}</Td>
                  <Td>
                    <Badge tone="neutral">
                      {t.purchasing[`method${payment.method}` as const]}
                    </Badge>
                  </Td>
                  <Td numeric>{formatMoney(payment.amount, payment.currency)}</Td>
                  <Td numeric>
                    {Number(payment.unapplied) === 0 ? (
                      '—'
                    ) : (
                      <span style={{ color: 'var(--warning)' }}>
                        {formatMoney(payment.unapplied, payment.currency)}
                      </span>
                    )}
                  </Td>
                  <Td>
                    <div className="flex justify-end">
                      {mayPay && Number(payment.unapplied) > 0 ? (
                        <Button size="sm" onClick={() => setApplying(payment)}>
                          {t.purchasing.applyAdvance}
                        </Button>
                      ) : null}
                    </div>
                  </Td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </Card>

      {applying ? (
        <ApplyAdvanceDialog
          payment={applying}
          onClose={() => setApplying(null)}
        />
      ) : null}

      {/* No invoice: the payment is held as an advance until one arrives,
          which is how this clinic buys most of what it buys. */}
      {paying ? (
        <PaySupplierDialog invoice={null} onClose={() => setPaying(false)} />
      ) : null}
    </div>
  )
}

function ApplyAdvanceDialog({
  payment,
  onClose,
}: {
  payment: SupplierPayment
  onClose: () => void
}) {
  const { t, formatMoney } = useI18n()
  const queryClient = useQueryClient()
  const [invoiceId, setInvoiceId] = useState('')
  const [amount, setAmount] = useState(payment.unapplied)
  const [error, setError] = useState<string | null>(null)

  // Only this supplier's registered bills with something left on them: an
  // advance cannot cross suppliers, and the server refuses it either way.
  const bills = useBills(
    { supplier_id: payment.supplier_id, status: 'REGISTERED' },
    true,
  )
  const payable = (bills.data ?? []).filter((bill) => Number(bill.remaining) > 0)

  const apply = useMutation({
    mutationFn: () =>
      api.post(`/purchasing/payments/${payment.id}/apply`, {
        invoice_id: invoiceId,
        amount: amount.trim(),
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['purchasing'] })
      onClose()
    },
    onError: (caught) => setError(describeError(caught, t)),
  })

  return (
    <Dialog
      open
      onClose={onClose}
      title={t.purchasing.applyAdvance}
      closeLabel={t.common.close}
      footer={
        <>
          <Button onClick={onClose} disabled={apply.isPending}>
            {t.common.cancel}
          </Button>
          <Button
            variant="primary"
            disabled={invoiceId === '' || amount.trim() === '' || apply.isPending}
            loading={apply.isPending}
            onClick={() => apply.mutate()}
          >
            {t.purchasing.applyAdvance}
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <p className="text-sm">
          {payment.code} · {t.purchasing.unapplied}:{' '}
          <span className="font-medium">
            {formatMoney(payment.unapplied, payment.currency)}
          </span>
        </p>

        {payable.length === 0 ? (
          <Alert tone="info">{t.purchasing.noBills}</Alert>
        ) : (
          <Field label={t.purchasing.againstBill} required htmlFor="apply-invoice">
            <Select
              id="apply-invoice"
              value={invoiceId}
              onChange={(event) => setInvoiceId(event.target.value)}
            >
              <option value="">—</option>
              {payable.map((bill) => (
                <option key={bill.id} value={bill.id}>
                  {bill.code} · {formatMoney(bill.remaining, bill.currency)}
                </option>
              ))}
            </Select>
          </Field>
        )}

        <Field label={t.purchasing.amount} required htmlFor="apply-amount">
          <Input
            id="apply-amount"
            inputMode="decimal"
            value={amount}
            onChange={(event) => setAmount(event.target.value)}
          />
        </Field>

        {error ? <Alert tone="danger">{error}</Alert> : null}
      </div>
    </Dialog>
  )
}
