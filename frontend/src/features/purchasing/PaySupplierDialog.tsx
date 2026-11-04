import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useI18n } from '@/i18n'
import { useAuth } from '@/features/auth/AuthProvider'
import { api } from '@/lib/api'
import { useCashSessions, useSuppliers } from '@/lib/queries'
import { describeError } from '@/lib/errorMessage'
import { todayInClinic } from '@/lib/clinicDate'
import {
  Alert,
  Button,
  Dialog,
  Field,
  Input,
  Select,
  Textarea,
} from '@/components/ui'
import type { SupplierInvoice, SupplierPaymentMethod } from '@/lib/types'

const METHODS: readonly SupplierPaymentMethod[] = ['CASH', 'BANK', 'CARD']

/**
 * Paying a supplier.
 *
 * The cash case is the one worth care: money leaving the till has to move
 * the drawer as well as the books, so the form asks which till it is coming
 * out of and refuses to submit without one. The server refuses too — but a
 * screen that let somebody press the button and then showed a 422 would be
 * teaching them to distrust it.
 *
 * With an invoice, the amount is capped at what is left on it. Without one,
 * the payment is held as an advance until a bill arrives, which is how this
 * clinic actually buys.
 */
export function PaySupplierDialog({
  supplierId: fixedSupplierId,
  invoice,
  onClose,
}: {
  /** Omitted when the payment is reached from the payables list, where
   *  nobody has chosen a supplier yet. */
  supplierId?: string
  /** The bill being settled, or `null` to pay an advance. */
  invoice: SupplierInvoice | null
  onClose: () => void
}) {
  const { t, formatMoney } = useI18n()
  const queryClient = useQueryClient()
  const { can } = useAuth()

  const [chosenSupplier, setChosenSupplier] = useState('')
  const supplierId = fixedSupplierId ?? chosenSupplier
  const suppliers = useSuppliers({}, fixedSupplierId === undefined)
  const [amount, setAmount] = useState(invoice ? invoice.remaining : '')
  const [method, setMethod] = useState<SupplierPaymentMethod>('BANK')
  const [paidOn, setPaidOn] = useState(todayInClinic())
  const [reference, setReference] = useState('')
  const [notes, setNotes] = useState('')
  const [sessionId, setSessionId] = useState('')
  const [error, setError] = useState<string | null>(null)

  // Only asked for when it could be used: a caller who cannot read cash
  // registers would get a 403 the screen would then have to explain away.
  const sessions = useCashSessions(
    method === 'CASH' && can('cash.register.read'),
    { session_status: 'OPEN' },
  )
  const openSessions = sessions.data ?? []

  const pay = useMutation({
    mutationFn: () =>
      api.post('/purchasing/payments', {
        supplier_id: supplierId,
        amount: amount.trim(),
        method,
        paid_on: paidOn,
        invoice_id: invoice ? invoice.id : null,
        reference: reference.trim() || null,
        notes: notes.trim() || null,
        cash_session_id: method === 'CASH' ? sessionId || null : null,
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['purchasing'] })
      void queryClient.invalidateQueries({ queryKey: ['cash'] })
      onClose()
    },
    onError: (caught) => setError(describeError(caught, t)),
  })

  const needsTill = method === 'CASH' && sessionId === ''
  const canSubmit =
    supplierId !== '' &&
    amount.trim() !== '' &&
    Number(amount) > 0 &&
    !needsTill &&
    !pay.isPending

  return (
    <Dialog
      open
      onClose={onClose}
      title={t.purchasing.pay}
      closeLabel={t.common.close}
      footer={
        <>
          <Button onClick={onClose} disabled={pay.isPending}>
            {t.common.cancel}
          </Button>
          <Button
            variant="primary"
            disabled={!canSubmit}
            loading={pay.isPending}
            onClick={() => pay.mutate()}
          >
            {t.purchasing.pay}
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <p className="text-sm text-[var(--text-secondary)]">{t.purchasing.payBody}</p>

        {fixedSupplierId === undefined ? (
          <Field label={t.purchasing.supplier} required htmlFor="pay-supplier">
            <Select
              id="pay-supplier"
              value={chosenSupplier}
              onChange={(event) => setChosenSupplier(event.target.value)}
            >
              <option value="">{t.purchasing.chooseSupplier}</option>
              {(suppliers.data ?? []).map((supplier) => (
                <option key={supplier.id} value={supplier.id}>
                  {supplier.name}
                </option>
              ))}
            </Select>
          </Field>
        ) : null}

        {invoice ? (
          <p className="text-sm">
            {invoice.code} · {t.purchasing.remaining}:{' '}
            <span className="font-medium">
              {formatMoney(invoice.remaining, invoice.currency)}
            </span>
          </p>
        ) : (
          <Alert tone="info">{t.purchasing.asAdvance}</Alert>
        )}

        <div className="grid gap-3 sm:grid-cols-2">
          <Field label={t.purchasing.amount} required htmlFor="pay-amount">
            <Input
              id="pay-amount"
              inputMode="decimal"
              value={amount}
              onChange={(event) => setAmount(event.target.value)}
            />
          </Field>
          <Field label={t.purchasing.method} htmlFor="pay-method">
            <Select
              id="pay-method"
              value={method}
              onChange={(event) =>
                setMethod(event.target.value as SupplierPaymentMethod)
              }
            >
              {METHODS.map((value) => (
                <option key={value} value={value}>
                  {t.purchasing[`method${value}` as const]}
                </option>
              ))}
            </Select>
          </Field>
          <Field label={t.purchasing.paidOn} htmlFor="pay-date">
            <Input
              id="pay-date"
              type="date"
              value={paidOn}
              onChange={(event) => setPaidOn(event.target.value)}
            />
          </Field>
          <Field label={t.purchasing.reference} htmlFor="pay-reference">
            <Input
              id="pay-reference"
              value={reference}
              onChange={(event) => setReference(event.target.value)}
            />
          </Field>
        </div>

        {method === 'CASH' ? (
          openSessions.length === 0 ? (
            <Alert tone="warning">{t.purchasing.cashNeedsTill}</Alert>
          ) : (
            <Field label={t.purchasing.tillSession} required htmlFor="pay-session">
              <Select
                id="pay-session"
                value={sessionId}
                onChange={(event) => setSessionId(event.target.value)}
              >
                <option value="">—</option>
                {openSessions.map((session) => (
                  <option key={session.id} value={session.id}>
                    {session.business_date}
                  </option>
                ))}
              </Select>
            </Field>
          )
        ) : null}

        <Field label={t.purchasing.notes} htmlFor="pay-notes">
          <Textarea
            id="pay-notes"
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
