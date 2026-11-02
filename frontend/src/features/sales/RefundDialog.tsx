import { useEffect, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useI18n } from '@/i18n'
import { useAuth } from '@/features/auth/AuthProvider'
import { api } from '@/lib/api'
import { useRefundability } from '@/lib/queries'
import { describeError } from '@/lib/errorMessage'
import { formatAmount } from '@/lib/money'
import { refundBody, refundPlan } from '@/lib/refund'
import {
  Alert,
  Button,
  DescriptionList,
  Dialog,
  Field,
  Input,
  LoadingState,
  Table,
  Td,
  Th,
} from '@/components/ui'

/**
 * Giving money back.
 *
 * One dialog for both refunds, because from the cashier's side it is one
 * decision — how much of this payment goes back to this patient — and the
 * difference between reversing a payment and handing part of it back is the
 * system's business, not theirs. It shows which it is going to do before it
 * does it.
 *
 * What it will not do:
 *
 * **It will not work out the refundable amount in the browser.** The figure
 * comes from `/sales/payments/{id}/refundability`, which derives it from the
 * payment, its allocations and its previous refunds. The dialog refetches it
 * every time it opens, because another cashier may have refunded something a
 * minute ago, and a stale figure would offer money that is no longer there.
 *
 * **It will not refund cash with no drawer open.** The money has to come out
 * of a till, and recording the ledger side without the drawer side leaves the
 * session expecting cash that was handed back — the day's count then reports
 * a shortage that is not one (ADR-040). With no session open it says so and
 * points at the cash screen instead of failing on submit.
 *
 * **It will not offer to refund money that is settling a bill.** That needs
 * the bill credited, and this clinic has no approved rule for a credit note,
 * so the dialog says which instrument is needed rather than pretending.
 */
export function RefundDialog({
  paymentId,
  openSessionId,
  onClose,
  onDone,
}: {
  /** The payment to give back. `null` keeps the dialog closed. */
  paymentId: string | null
  /** The open cash session, where one is open. Required for cash. */
  openSessionId: string | null
  onClose: () => void
  onDone: (message: string) => void
}) {
  const { t, formatDate } = useI18n()
  const { can } = useAuth()
  const queryClient = useQueryClient()

  const [typed, setTyped] = useState('')
  const [reason, setReason] = useState('')
  const [error, setError] = useState<string | null>(null)

  // Approval authority, or the request side of it where the clinic has
  // declared it cannot separate duties — the same test the server makes.
  const mayRefund = can('sales.refund.approve') || can('sales.refund.request')
  const figures = useRefundability(paymentId)

  useEffect(() => {
    if (paymentId !== null) {
      setTyped('')
      setReason('')
      setError(null)
    }
  }, [paymentId])

  const submit = useMutation({
    mutationFn: () => {
      if (view === null) throw new Error('no payment')
      const body = refundBody(plan, reason)
      // Cash leaves a drawer, so it goes through the session that holds it.
      // Everything else has no drawer to come out of.
      return view.needs_cash_session
        ? api.post(`/cash/sessions/${openSessionId}/refunds`, {
            payment_id: paymentId,
            ...body,
          })
        : api.post(`/sales/payments/${paymentId}/refund`, body)
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['sales'] })
      void queryClient.invalidateQueries({ queryKey: ['cash'] })
      void queryClient.invalidateQueries({ queryKey: ['clinic'] })
      onDone(
        t.refunds.doneNotice
          .replace('{amount}', formatAmount(plan.amount))
          .replace('{number}', view?.payment.number ?? ''),
      )
    },
    onError: (caught) => setError(describeError(caught, t)),
  })

  const view = figures.data ?? null
  const plan = refundPlan({
    view:
      view ?? {
        amount: '0',
        allocated: '0',
        refunded: '0',
        refundable: '0',
        currency: 'UZS',
        may_refund: false,
        needs_cash_session: false,
      },
    typed,
    openSessionId,
    mayRefund,
  })

  const blockedMessage: Record<string, string> = {
    alreadyRefunded: t.refunds.blockedAlreadyRefunded,
    allOnBills: t.refunds.blockedAllOnBills,
    needsOpenDrawer: t.refunds.blockedNeedsDrawer,
    noPermission: t.refunds.blockedNoPermission,
  }
  const errorMessage: Record<string, string> = {
    notANumber: t.refunds.errorNotANumber,
    mustBePositive: t.refunds.errorMustBePositive,
    overRefundable: t.refunds.errorOverRefundable.replace(
      '{amount}',
      view ? view.refundable : '0',
    ),
  }

  return (
    <Dialog
      open={paymentId !== null}
      onClose={onClose}
      title={`${t.refunds.title}${view ? `: ${view.payment.number}` : ''}`}
      closeLabel={t.common.close}
      footer={
        <>
          <Button onClick={onClose} disabled={submit.isPending}>
            {t.common.cancel}
          </Button>
          <Button
            id="refund-submit"
            variant="danger"
            loading={submit.isPending}
            disabled={!plan.submittable || !reason.trim()}
            onClick={() => {
              setError(null)
              submit.mutate()
            }}
          >
            {plan.mode === 'full' ? t.refunds.reverseAction : t.refunds.refundAction}
          </Button>
        </>
      }
    >
      {figures.isLoading ? <LoadingState label={t.common.loading} /> : null}

      {view !== null ? (
        <div className="space-y-4">
          <DescriptionList
            items={[
              { label: t.refunds.originalAmount, value: view.amount },
              { label: t.refunds.onBills, value: view.allocated },
              { label: t.refunds.alreadyGivenBack, value: view.refunded },
              {
                label: t.refunds.refundableNow,
                value: (
                  <span id="refund-refundable" className="font-mono">
                    {view.refundable}
                  </span>
                ),
              },
            ]}
          />

          {view.refunds.length > 0 ? (
            <div>
              <h3 className="mb-2 text-sm font-semibold text-[var(--text-primary)]">
                {t.refunds.previous}
              </h3>
              <Table>
                <thead>
                  <tr>
                    <Th>{t.refunds.refundNumber}</Th>
                    <Th>{t.sales.receivedOn}</Th>
                    <Th numeric>{t.sales.amount}</Th>
                    <Th>{t.refunds.reason}</Th>
                  </tr>
                </thead>
                <tbody>
                  {view.refunds.map((row) => (
                    <tr key={row.id}>
                      <Td>
                        <span className="font-mono text-xs">{row.number}</span>
                      </Td>
                      <Td>{formatDate(row.refunded_on)}</Td>
                      <Td numeric>{row.amount}</Td>
                      <Td>{row.reason}</Td>
                    </tr>
                  ))}
                </tbody>
              </Table>
            </div>
          ) : null}

          {plan.blocked !== null ? (
            <Alert tone="warning">{blockedMessage[plan.blocked]}</Alert>
          ) : (
            <>
              <Field
                label={t.refunds.amountToRefund}
                htmlFor="refund-amount"
                hint={t.refunds.amountHint.replace('{amount}', view.refundable)}
                error={plan.error ? errorMessage[plan.error] : undefined}
              >
                <Input
                  id="refund-amount"
                  inputMode="decimal"
                  value={typed}
                  placeholder={view.refundable}
                  onChange={(event) => setTyped(event.target.value)}
                />
              </Field>

              <Field label={t.refunds.reason} htmlFor="refund-reason" required>
                <Input
                  id="refund-reason"
                  value={reason}
                  onChange={(event) => setReason(event.target.value)}
                />
              </Field>

              {/* Which of the two refunds this is, before it happens. */}
              <Alert tone={plan.mode === 'full' ? 'warning' : 'info'}>
                {plan.mode === 'full'
                  ? t.refunds.willReverse
                  : t.refunds.willRefundPart
                      .replace('{amount}', formatAmount(plan.amount))
                      .replace('{remaining}', formatAmount(plan.remaining))}
              </Alert>
            </>
          )}

          {error ? <Alert tone="danger">{error}</Alert> : null}
        </div>
      ) : null}
    </Dialog>
  )
}
