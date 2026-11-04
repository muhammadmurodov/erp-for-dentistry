import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useI18n } from '@/i18n'
import { useAuth } from '@/features/auth/AuthProvider'
import { api } from '@/lib/api'
import { useBill, useBills, useSuppliers, useUninvoicedLines } from '@/lib/queries'
import { describeError, errorRequestId } from '@/lib/errorMessage'
import { billIsPayable, lineTotal, sumAmounts } from '@/lib/purchasing'
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
import type { BillKind, BillStatus, SupplierInvoice } from '@/lib/types'
import { PaySupplierDialog } from './PaySupplierDialog'

/**
 * Supplier invoices: entered as drafts, registered into the books, paid.
 *
 * Registering is where the payable appears and the posting happens, and from
 * that moment the invoice is immutable — correcting one means voiding it and
 * entering the corrected one, which is why the screen separates the two acts
 * instead of offering an edit button that would quietly stop working.
 */
export function BillsPanel() {
  const { t, formatDate, formatMoney } = useI18n()
  const { can } = useAuth()
  const mayManage = can('purchasing.bill.manage')

  const [status, setStatus] = useState('')
  const [openId, setOpenId] = useState<string | null>(null)
  const [creating, setCreating] = useState(false)

  const bills = useBills({ status: status || undefined }, true)

  if (openId) {
    return <BillDetail invoiceId={openId} onBack={() => setOpenId(null)} />
  }

  return (
    <>
      <Card>
        <CardHeader
          title={t.purchasing.billsTab}
          actions={
            <div className="flex flex-wrap items-center gap-2">
              <label className="sr-only" htmlFor="bill-status">
                {t.purchasing.billStatus}
              </label>
              <Select
                id="bill-status"
                value={status}
                onChange={(event) => setStatus(event.target.value)}
                className="w-auto"
              >
                <option value="">{t.common.all}</option>
                {(['DRAFT', 'REGISTERED', 'VOID'] as BillStatus[]).map((value) => (
                  <option key={value} value={value}>
                    {t.purchasing[`bill${value}` as const]}
                  </option>
                ))}
              </Select>
              {mayManage ? (
                <Button size="sm" variant="primary" onClick={() => setCreating(true)}>
                  {t.purchasing.newBill}
                </Button>
              ) : null}
            </div>
          }
        />
        {bills.isLoading ? (
          <LoadingState label={t.common.loading} />
        ) : bills.isError ? (
          <ErrorState
            title={t.errors.loadFailedTitle}
            message={describeError(bills.error, t)}
            requestId={errorRequestId(bills.error)}
            requestIdLabel={t.common.requestId}
            onRetry={() => void bills.refetch()}
            retryLabel={t.common.retry}
          />
        ) : (bills.data ?? []).length === 0 ? (
          <EmptyState title={t.purchasing.noBills} />
        ) : (
          <Table>
            <thead>
              <tr>
                <Th>{t.purchasing.reference}</Th>
                <Th>{t.purchasing.supplier}</Th>
                <Th>{t.purchasing.invoiceDate}</Th>
                <Th>{t.purchasing.dueDate}</Th>
                <Th numeric>{t.purchasing.total}</Th>
                <Th numeric>{t.purchasing.remaining}</Th>
                <Th>{t.purchasing.billStatus}</Th>
                <Th>{t.common.actions}</Th>
              </tr>
            </thead>
            <tbody>
              {(bills.data ?? []).map((bill) => (
                <tr key={bill.id}>
                  <Td>
                    <span className="font-mono text-xs">{bill.code}</span>
                  </Td>
                  <Td>{bill.supplier_name}</Td>
                  <Td>{formatDate(bill.invoice_date)}</Td>
                  <Td>{bill.due_date ? formatDate(bill.due_date) : '—'}</Td>
                  <Td numeric>{formatMoney(bill.total, bill.currency)}</Td>
                  <Td numeric>
                    {bill.status === 'REGISTERED'
                      ? formatMoney(bill.remaining, bill.currency)
                      : '—'}
                  </Td>
                  <Td>
                    <BillBadge status={bill.status} />
                  </Td>
                  <Td>
                    <div className="flex justify-end">
                      <Button size="sm" onClick={() => setOpenId(bill.id)}>
                        {t.purchasing.open}
                      </Button>
                    </div>
                  </Td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </Card>

      {creating ? (
        <NewBillDialog
          onClose={() => setCreating(false)}
          onCreated={(bill) => {
            setCreating(false)
            setOpenId(bill.id)
          }}
        />
      ) : null}
    </>
  )
}

function BillBadge({ status }: { status: BillStatus }) {
  const { t } = useI18n()
  const tone: Record<BillStatus, 'neutral' | 'success' | 'warning'> = {
    DRAFT: 'neutral',
    REGISTERED: 'success',
    VOID: 'warning',
  }
  return <Badge tone={tone[status]}>{t.purchasing[`bill${status}` as const]}</Badge>
}

function BillDetail({
  invoiceId,
  onBack,
}: {
  invoiceId: string
  onBack: () => void
}) {
  const { t, formatDate, formatMoney } = useI18n()
  const queryClient = useQueryClient()
  const { can } = useAuth()
  const bill = useBill(invoiceId, true)
  const [error, setError] = useState<string | null>(null)
  const [voiding, setVoiding] = useState(false)
  const [reason, setReason] = useState('')
  const [paying, setPaying] = useState(false)

  const mayManage = can('purchasing.bill.manage')
  const mayPay = can('purchasing.payment.record')

  const invalidate = () =>
    void queryClient.invalidateQueries({ queryKey: ['purchasing'] })

  const register = useMutation({
    mutationFn: () => api.post(`/purchasing/bills/${invoiceId}/register`),
    onSuccess: () => {
      setError(null)
      invalidate()
    },
    onError: (caught) => setError(describeError(caught, t)),
  })

  const voidBill = useMutation({
    mutationFn: () =>
      api.post(`/purchasing/bills/${invoiceId}/void`, { reason: reason.trim() }),
    onSuccess: () => {
      setVoiding(false)
      setReason('')
      setError(null)
      invalidate()
    },
    onError: (caught) => setError(describeError(caught, t)),
  })

  if (bill.isLoading) return <LoadingState label={t.common.loading} />
  if (bill.isError || !bill.data) {
    return (
      <ErrorState
        title={t.errors.loadFailedTitle}
        message={describeError(bill.error, t)}
        requestId={errorRequestId(bill.error)}
        requestIdLabel={t.common.requestId}
        onRetry={() => void bill.refetch()}
        retryLabel={t.common.retry}
      />
    )
  }

  const detail = bill.data

  return (
    <div className="space-y-4">
      <Button size="sm" onClick={onBack}>
        ← {t.purchasing.back}
      </Button>

      <Card>
        <CardHeader
          title={`${detail.code} · ${detail.supplier_name}`}
          actions={<BillBadge status={detail.status} />}
        />
        <DescriptionList
          items={[
            {
              label: t.purchasing.billKind,
              value: t.purchasing[`kind${detail.kind}` as const],
            },
            {
              label: t.purchasing.supplierReference,
              value: detail.supplier_reference ?? '—',
            },
            { label: t.purchasing.invoiceDate, value: formatDate(detail.invoice_date) },
            {
              label: t.purchasing.dueDate,
              value: detail.due_date ? formatDate(detail.due_date) : '—',
            },
            {
              label: t.purchasing.total,
              value: formatMoney(detail.total, detail.currency),
            },
            ...(detail.status === 'REGISTERED'
              ? [
                  {
                    label: t.purchasing.paidSoFar,
                    value: formatMoney(detail.paid, detail.currency),
                  },
                  {
                    label: t.purchasing.remaining,
                    value:
                      Number(detail.remaining) === 0 ? (
                        formatMoney(detail.remaining, detail.currency)
                      ) : (
                        <span style={{ color: 'var(--warning)' }}>
                          {formatMoney(detail.remaining, detail.currency)}
                        </span>
                      ),
                  },
                ]
              : []),
          ]}
        />

        <div className="space-y-3 px-4 pb-4">
          {detail.status === 'VOID' ? (
            <Alert tone="warning">
              {t.purchasing.voidedNotice}
              {detail.void_reason ? ` ${detail.void_reason}` : ''}
            </Alert>
          ) : null}

          <div className="flex flex-wrap gap-2">
            {mayManage && detail.status === 'DRAFT' ? (
              <Button
                size="sm"
                variant="primary"
                loading={register.isPending}
                onClick={() => register.mutate()}
              >
                {t.purchasing.register}
              </Button>
            ) : null}
            {mayPay && billIsPayable(detail) ? (
              <Button size="sm" variant="primary" onClick={() => setPaying(true)}>
                {t.purchasing.pay}
              </Button>
            ) : null}
            {mayManage && detail.status !== 'VOID' ? (
              <Button size="sm" variant="ghost" onClick={() => setVoiding(true)}>
                {t.purchasing.void}
              </Button>
            ) : null}
          </div>

          {detail.status === 'DRAFT' ? (
            <p className="text-sm text-[var(--text-secondary)]">
              {t.purchasing.registerBody}
            </p>
          ) : null}

          {error ? <Alert tone="danger">{error}</Alert> : null}
        </div>
      </Card>

      <Card>
        <CardHeader title={t.purchasing.description} />
        <Table>
          <thead>
            <tr>
              <Th>{t.purchasing.description}</Th>
              <Th numeric>{t.purchasing.quantity}</Th>
              <Th numeric>{t.purchasing.unitPrice}</Th>
              <Th numeric>{t.purchasing.lineTotal}</Th>
            </tr>
          </thead>
          <tbody>
            {detail.lines.map((line) => (
              <tr key={line.id}>
                <Td>{line.description}</Td>
                <Td numeric>
                  {line.quantity}
                  {line.unit ? ` ${line.unit}` : ''}
                </Td>
                <Td numeric>{formatMoney(line.unit_price, detail.currency)}</Td>
                <Td numeric>{formatMoney(line.line_total, detail.currency)}</Td>
              </tr>
            ))}
          </tbody>
        </Table>
      </Card>

      <Dialog
        open={voiding}
        onClose={() => setVoiding(false)}
        title={t.purchasing.void}
        closeLabel={t.common.close}
        footer={
          <>
            <Button onClick={() => setVoiding(false)} disabled={voidBill.isPending}>
              {t.common.cancel}
            </Button>
            <Button
              variant="danger"
              disabled={reason.trim().length < 3 || voidBill.isPending}
              loading={voidBill.isPending}
              onClick={() => voidBill.mutate()}
            >
              {t.purchasing.void}
            </Button>
          </>
        }
      >
        <div className="space-y-3">
          <p className="text-sm text-[var(--text-secondary)]">{t.purchasing.voidBody}</p>
          <Field label={t.clinic.reason} required htmlFor="void-reason">
            <Textarea
              id="void-reason"
              rows={2}
              value={reason}
              onChange={(event) => setReason(event.target.value)}
            />
          </Field>
        </div>
      </Dialog>

      {paying ? (
        <PaySupplierDialog
          supplierId={detail.supplier_id}
          invoice={detail}
          onClose={() => setPaying(false)}
        />
      ) : null}
    </div>
  )
}

// ------------------------------------------------------------------- dialog

interface DraftBillLine {
  key: number
  description: string
  quantity: string
  unitPrice: string
  receiptLineId: string
}

function NewBillDialog({
  onClose,
  onCreated,
}: {
  onClose: () => void
  onCreated: (bill: SupplierInvoice) => void
}) {
  const { t, formatMoney } = useI18n()
  const queryClient = useQueryClient()
  const suppliers = useSuppliers({}, true)
  const [supplierId, setSupplierId] = useState('')
  const [kind, setKind] = useState<BillKind>('MATERIALS')
  const [invoiceDate, setInvoiceDate] = useState(todayInClinic())
  const [dueDate, setDueDate] = useState('')
  const [reference, setReference] = useState('')
  const [rows, setRows] = useState<DraftBillLine[]>([
    { key: 1, description: '', quantity: '1', unitPrice: '', receiptLineId: '' },
  ])
  const [error, setError] = useState<string | null>(null)

  const uninvoiced = useUninvoicedLines(supplierId || null, supplierId !== '')

  const usable = rows.filter(
    (row) => row.description.trim() !== '' && row.unitPrice.trim() !== '',
  )
  const total = sumAmounts(usable.map((row) => lineTotal(row.quantity, row.unitPrice)))

  const create = useMutation({
    mutationFn: () =>
      api.post<SupplierInvoice>('/purchasing/bills', {
        supplier_id: supplierId,
        kind,
        invoice_date: invoiceDate,
        due_date: dueDate || null,
        supplier_reference: reference.trim() || null,
        lines: usable.map((row) => ({
          description: row.description.trim(),
          quantity: row.quantity.trim(),
          unit_price: row.unitPrice.trim(),
          receipt_line_id: row.receiptLineId || null,
        })),
      }),
    onSuccess: (bill) => {
      void queryClient.invalidateQueries({ queryKey: ['purchasing'] })
      onCreated(bill)
    },
    onError: (caught) => setError(describeError(caught, t)),
  })

  /** Fill a line from a delivery nobody has billed for yet. */
  const takeFromDelivery = (key: number, lineId: string) => {
    const line = (uninvoiced.data ?? []).find((l) => l.id === lineId)
    setRows((current) =>
      current.map((r) =>
        r.key === key
          ? {
              ...r,
              receiptLineId: lineId,
              description: line ? line.description : r.description,
              quantity: line ? line.quantity : r.quantity,
            }
          : r,
      ),
    )
  }

  return (
    <Dialog
      open
      size="lg"
      onClose={onClose}
      title={t.purchasing.newBill}
      closeLabel={t.common.close}
      footer={
        <>
          <Button onClick={onClose} disabled={create.isPending}>
            {t.common.cancel}
          </Button>
          <Button
            variant="primary"
            disabled={supplierId === '' || usable.length === 0 || create.isPending}
            loading={create.isPending}
            onClick={() => create.mutate()}
          >
            {t.common.save}
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <p className="text-sm text-[var(--text-secondary)]">{t.purchasing.newBillBody}</p>

        <div className="grid gap-3 sm:grid-cols-2">
          <Field label={t.purchasing.supplier} required htmlFor="bill-supplier">
            <Select
              id="bill-supplier"
              value={supplierId}
              onChange={(event) => setSupplierId(event.target.value)}
            >
              <option value="">{t.purchasing.chooseSupplier}</option>
              {(suppliers.data ?? []).map((supplier) => (
                <option key={supplier.id} value={supplier.id}>
                  {supplier.name}
                </option>
              ))}
            </Select>
          </Field>
          <Field
            label={t.purchasing.billKind}
            hint={t.purchasing.kindHint}
            htmlFor="bill-kind"
          >
            <Select
              id="bill-kind"
              value={kind}
              onChange={(event) => setKind(event.target.value as BillKind)}
            >
              <option value="MATERIALS">{t.purchasing.kindMATERIALS}</option>
              <option value="EXPENSE">{t.purchasing.kindEXPENSE}</option>
            </Select>
          </Field>
          <Field label={t.purchasing.invoiceDate} htmlFor="bill-date">
            <Input
              id="bill-date"
              type="date"
              value={invoiceDate}
              onChange={(event) => setInvoiceDate(event.target.value)}
            />
          </Field>
          <Field label={t.purchasing.dueDate} htmlFor="bill-due">
            <Input
              id="bill-due"
              type="date"
              value={dueDate}
              onChange={(event) => setDueDate(event.target.value)}
            />
          </Field>
        </div>

        <Field
          label={t.purchasing.supplierReference}
          hint={t.purchasing.supplierReferenceHint}
          htmlFor="bill-reference"
        >
          <Input
            id="bill-reference"
            value={reference}
            onChange={(event) => setReference(event.target.value)}
          />
        </Field>

        <div className="space-y-2">
          {rows.map((row) => (
            <div
              key={row.key}
              className="grid gap-2 rounded-lg border border-[var(--border-subtle)] p-3 sm:grid-cols-[2fr_5rem_7rem]"
            >
              {(uninvoiced.data ?? []).length > 0 ? (
                <div className="sm:col-span-3">
                  <Field
                    label={t.purchasing.uninvoicedHeading}
                    htmlFor={`bill-delivery-${row.key}`}
                  >
                    <Select
                      id={`bill-delivery-${row.key}`}
                      value={row.receiptLineId}
                      onChange={(event) =>
                        takeFromDelivery(row.key, event.target.value)
                      }
                    >
                      <option value="">—</option>
                      {(uninvoiced.data ?? []).map((line) => (
                        <option key={line.id} value={line.id}>
                          {line.receipt_code} · {line.description} · {line.quantity}
                        </option>
                      ))}
                    </Select>
                  </Field>
                </div>
              ) : null}
              <Field
                label={t.purchasing.description}
                htmlFor={`bill-line-desc-${row.key}`}
              >
                <Input
                  id={`bill-line-desc-${row.key}`}
                  value={row.description}
                  onChange={(event) =>
                    setRows((current) =>
                      current.map((r) =>
                        r.key === row.key
                          ? { ...r, description: event.target.value }
                          : r,
                      ),
                    )
                  }
                />
              </Field>
              <Field
                label={t.purchasing.quantity}
                htmlFor={`bill-line-qty-${row.key}`}
              >
                <Input
                  id={`bill-line-qty-${row.key}`}
                  inputMode="decimal"
                  value={row.quantity}
                  onChange={(event) =>
                    setRows((current) =>
                      current.map((r) =>
                        r.key === row.key ? { ...r, quantity: event.target.value } : r,
                      ),
                    )
                  }
                />
              </Field>
              <Field
                label={t.purchasing.unitPrice}
                htmlFor={`bill-line-price-${row.key}`}
                hint={
                  row.unitPrice.trim() === ''
                    ? undefined
                    : formatMoney(lineTotal(row.quantity, row.unitPrice))
                }
              >
                <Input
                  id={`bill-line-price-${row.key}`}
                  inputMode="decimal"
                  value={row.unitPrice}
                  onChange={(event) =>
                    setRows((current) =>
                      current.map((r) =>
                        r.key === row.key ? { ...r, unitPrice: event.target.value } : r,
                      ),
                    )
                  }
                />
              </Field>
            </div>
          ))}
        </div>

        <div className="flex flex-wrap items-center justify-between gap-2">
          <Button
            size="sm"
            onClick={() =>
              setRows((current) => [
                ...current,
                {
                  key: Math.max(0, ...current.map((r) => r.key)) + 1,
                  description: '',
                  quantity: '1',
                  unitPrice: '',
                  receiptLineId: '',
                },
              ])
            }
          >
            {t.purchasing.addLine}
          </Button>
          <p className="text-sm">
            {t.purchasing.total}:{' '}
            <span className="font-medium">{formatMoney(total)}</span>
          </p>
        </div>

        {error ? <Alert tone="danger">{error}</Alert> : null}
      </div>
    </Dialog>
  )
}
