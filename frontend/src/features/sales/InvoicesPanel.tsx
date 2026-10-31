import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useI18n } from '@/i18n'
import { useAuth } from '@/features/auth/AuthProvider'
import { api } from '@/lib/api'
import { useCustomers, useInvoice, useInvoices, useServices } from '@/lib/queries'
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
import type { Invoice, InvoiceDetail, InvoiceStatus } from '@/lib/types'

export function InvoiceStatusBadge({ status }: { status: InvoiceStatus }) {
  const { t } = useI18n()
  switch (status) {
    case 'PAID':
      return <Badge tone="success">{t.sales.paid}</Badge>
    case 'PARTIALLY_PAID':
      return <Badge tone="warning">{t.sales.partiallyPaid}</Badge>
    case 'ISSUED':
      return <Badge tone="brand">{t.sales.issued}</Badge>
    case 'VOID':
      return <Badge tone="danger">{t.sales.void}</Badge>
    case 'CANCELLED':
      return <Badge>{t.sales.cancelled}</Badge>
    default:
      return <Badge>{t.sales.draft}</Badge>
  }
}

export function InvoicesPanel() {
  const { t, formatDate } = useI18n()
  const { can } = useAuth()
  const queryClient = useQueryClient()

  const mayRead = can('sales.invoice.read')
  const mayManage = can('sales.invoice.manage')
  const mayVoid = can('sales.refund.approve')

  const invoices = useInvoices(mayRead)
  const [createOpen, setCreateOpen] = useState(false)
  const [detailId, setDetailId] = useState<string | null>(null)
  const [voidTarget, setVoidTarget] = useState<Invoice | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const invalidate = () => void queryClient.invalidateQueries({ queryKey: ['sales'] })

  const issue = useMutation({
    mutationFn: (id: string) =>
      api.post<InvoiceDetail>(`/sales/invoices/${id}/issue`, {
        // Stated explicitly: the system will not pick a tax treatment.
        tax_treatment: 'NONE',
      }),
    onSuccess: (invoice) => {
      setError(null)
      setNotice(`${t.sales.issued2} ${invoice.number}`)
      invalidate()
    },
    onError: (err) => setError(describeError(err, t)),
  })

  if (!mayRead) return <Alert tone="warning">{t.moduleStatus.noAccess}</Alert>
  if (invoices.isLoading) return <LoadingState label={t.common.loading} />
  if (invoices.isError) {
    return (
      <ErrorState
        title={t.errors.loadFailedTitle}
        message={describeError(invoices.error, t)}
        requestId={errorRequestId(invoices.error)}
        requestIdLabel={t.common.requestId}
        onRetry={() => void invoices.refetch()}
        retryLabel={t.common.retry}
      />
    )
  }

  const rows = invoices.data ?? []

  return (
    <>
      <Card>
        <CardHeader
          title={t.sales.invoicesTab}
          actions={
            mayManage ? (
              <Button variant="primary" size="sm" onClick={() => setCreateOpen(true)}>
                {t.sales.newInvoice}
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
          <EmptyState title={t.sales.emptyInvoices} />
        ) : (
          <Table>
            <thead>
              <tr>
                <Th>{t.sales.invoiceNumber}</Th>
                <Th>{t.sales.issueDate}</Th>
                <Th numeric>{t.sales.total}</Th>
                <Th numeric>{t.sales.allocated}</Th>
                <Th numeric>{t.sales.outstanding}</Th>
                <Th>{t.sales.status}</Th>
                <Th>{t.common.actions}</Th>
              </tr>
            </thead>
            <tbody>
              {rows.map((invoice) => (
                <tr key={invoice.id}>
                  <Td>
                    <button
                      type="button"
                      className="font-mono text-xs underline underline-offset-2"
                      onClick={() => setDetailId(invoice.id)}
                    >
                      {invoice.number}
                    </button>
                  </Td>
                  <Td>{formatDate(invoice.issue_date)}</Td>
                  <Td numeric>{invoice.total}</Td>
                  <Td numeric>{invoice.allocated}</Td>
                  <Td numeric>{invoice.outstanding}</Td>
                  <Td>
                    <InvoiceStatusBadge status={invoice.status} />
                  </Td>
                  <Td>
                    <div className="flex flex-wrap gap-1.5">
                      {invoice.status === 'DRAFT' && mayManage ? (
                        <Button
                          size="sm"
                          variant="ghost"
                          loading={issue.isPending}
                          onClick={() => issue.mutate(invoice.id)}
                        >
                          {t.sales.issue}
                        </Button>
                      ) : null}
                      {invoice.status !== 'DRAFT' &&
                      invoice.status !== 'VOID' &&
                      mayVoid ? (
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => {
                            setError(null)
                            setVoidTarget(invoice)
                          }}
                        >
                          {t.sales.voidInvoice}
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

      <CreateInvoiceDialog
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onCreated={(message) => {
          setCreateOpen(false)
          setError(null)
          setNotice(message)
          invalidate()
        }}
      />

      <InvoiceDetailDialog id={detailId} onClose={() => setDetailId(null)} />

      <VoidDialog
        invoice={voidTarget}
        onClose={() => setVoidTarget(null)}
        onDone={(message) => {
          setVoidTarget(null)
          setNotice(message)
          invalidate()
        }}
        onError={(message) => {
          setVoidTarget(null)
          setError(message)
        }}
      />
    </>
  )
}

interface DraftLine {
  service_id: string
  description: string
  quantity: string
  unit_price: string
}

function CreateInvoiceDialog({
  open,
  onClose,
  onCreated,
}: {
  open: boolean
  onClose: () => void
  onCreated: (message: string) => void
}) {
  const { t } = useI18n()
  const customers = useCustomers(open)
  const services = useServices(open)

  const [customerId, setCustomerId] = useState('')
  const [issueDate, setIssueDate] = useState(() =>
    new Date().toISOString().slice(0, 10),
  )
  const [dueDate, setDueDate] = useState('')
  const [lines, setLines] = useState<DraftLine[]>([
    { service_id: '', description: '', quantity: '1', unit_price: '' },
  ])
  const [formError, setFormError] = useState<string | null>(null)

  const create = useMutation({
    mutationFn: () =>
      api.post<InvoiceDetail>('/sales/invoices', {
        customer_id: customerId,
        issue_date: issueDate,
        due_date: dueDate || null,
        lines: lines
          .filter((line) => line.description || line.service_id)
          .map((line) => ({
            service_id: line.service_id || null,
            description: line.description || '—',
            quantity: line.quantity || '1',
            // Omitted so the price list supplies it.
            unit_price: line.unit_price || null,
          })),
      }),
    onSuccess: (invoice) => {
      setLines([{ service_id: '', description: '', quantity: '1', unit_price: '' }])
      setFormError(null)
      onCreated(`${t.sales.invoiceCreated} ${invoice.number}`)
    },
    onError: (err) => setFormError(describeError(err, t)),
  })

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={t.sales.newInvoice}
      closeLabel={t.common.close}
      size="lg"
      footer={
        <>
          <Button onClick={onClose} disabled={create.isPending}>
            {t.common.cancel}
          </Button>
          <Button
            variant="primary"
            loading={create.isPending}
            disabled={!customerId}
            onClick={() => {
              setFormError(null)
              create.mutate()
            }}
          >
            {t.common.create}
          </Button>
        </>
      }
    >
      {formError ? (
        <div className="mb-4">
          <Alert tone="danger">{formError}</Alert>
        </div>
      ) : null}

      <div className="flex min-w-0 flex-col gap-4">
        <div className="grid gap-4 sm:grid-cols-3">
          <Field label={t.sales.customer} htmlFor="inv-customer" required>
            <Select
              id="inv-customer"
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
          <Field label={t.sales.issueDate} htmlFor="inv-date" required>
            <Input
              id="inv-date"
              type="date"
              value={issueDate}
              onChange={(event) => setIssueDate(event.target.value)}
            />
          </Field>
          <Field label={t.sales.dueDate} htmlFor="inv-due">
            <Input
              id="inv-due"
              type="date"
              value={dueDate}
              onChange={(event) => setDueDate(event.target.value)}
            />
          </Field>
        </div>

        <div className="flex min-w-0 flex-col gap-2">
          <div className="grid grid-cols-[minmax(0,1fr)_6rem_8rem_2.5rem] gap-2 text-xs font-semibold text-[var(--text-secondary)]">
            <span>{t.sales.service}</span>
            <span className="text-right">{t.sales.quantity}</span>
            <span className="text-right">{t.sales.unitPrice}</span>
            <span />
          </div>
          {lines.map((line, index) => (
            <div
              key={index}
              className="grid grid-cols-[minmax(0,1fr)_6rem_8rem_2.5rem] items-center gap-2"
            >
              <Select
                className="w-full min-w-0"
                aria-label={`${t.sales.service} ${index + 1}`}
                value={line.service_id}
                onChange={(event) => {
                  const service = (services.data ?? []).find(
                    (s) => s.id === event.target.value,
                  )
                  setLines((current) =>
                    current.map((l, i) =>
                      i === index
                        ? {
                            ...l,
                            service_id: event.target.value,
                            description: service?.name ?? l.description,
                          }
                        : l,
                    ),
                  )
                }}
              >
                <option value="">—</option>
                {(services.data ?? []).map((service) => (
                  <option key={service.id} value={service.id}>
                    {service.code} · {service.name}
                    {service.current_price ? ` · ${service.current_price}` : ''}
                  </option>
                ))}
              </Select>
              <Input
                className="tabular w-full min-w-0 text-right"
                aria-label={`${t.sales.quantity} ${index + 1}`}
                inputMode="decimal"
                value={line.quantity}
                onChange={(event) =>
                  setLines((current) =>
                    current.map((l, i) =>
                      i === index ? { ...l, quantity: event.target.value } : l,
                    ),
                  )
                }
              />
              <Input
                className="tabular w-full min-w-0 text-right"
                aria-label={`${t.sales.unitPrice} ${index + 1}`}
                inputMode="decimal"
                placeholder={t.sales.price}
                value={line.unit_price}
                onChange={(event) =>
                  setLines((current) =>
                    current.map((l, i) =>
                      i === index ? { ...l, unit_price: event.target.value } : l,
                    ),
                  )
                }
              />
              <Button
                size="sm"
                variant="ghost"
                disabled={lines.length <= 1}
                onClick={() =>
                  setLines((current) => current.filter((_, i) => i !== index))
                }
              >
                ✕
              </Button>
            </div>
          ))}
          <div>
            <Button
              size="sm"
              onClick={() =>
                setLines((current) => [
                  ...current,
                  { service_id: '', description: '', quantity: '1', unit_price: '' },
                ])
              }
            >
              {t.sales.addLine}
            </Button>
          </div>
        </div>
      </div>
    </Dialog>
  )
}

function InvoiceDetailDialog({
  id,
  onClose,
}: {
  id: string | null
  onClose: () => void
}) {
  const { t, formatDate } = useI18n()
  const invoice = useInvoice(id)

  return (
    <Dialog
      open={id !== null}
      onClose={onClose}
      title={invoice.data ? invoice.data.number : t.common.loading}
      closeLabel={t.common.close}
      size="lg"
      footer={<Button onClick={onClose}>{t.common.close}</Button>}
    >
      {invoice.isLoading ? (
        <LoadingState label={t.common.loading} />
      ) : invoice.data ? (
        <div className="flex flex-col gap-3">
          <dl className="grid grid-cols-[9rem_1fr] gap-x-4 gap-y-1 text-sm">
            <dt className="text-[var(--text-secondary)]">{t.sales.customer}</dt>
            <dd>{invoice.data.customer_name}</dd>
            <dt className="text-[var(--text-secondary)]">{t.sales.issueDate}</dt>
            <dd>{formatDate(invoice.data.issue_date)}</dd>
            <dt className="text-[var(--text-secondary)]">{t.sales.outstanding}</dt>
            <dd className="tabular font-medium">{invoice.data.outstanding}</dd>
            {invoice.data.void_reason ? (
              <>
                <dt className="text-[var(--text-secondary)]">{t.sales.reason}</dt>
                <dd>{invoice.data.void_reason}</dd>
              </>
            ) : null}
          </dl>

          <Table>
            <thead>
              <tr>
                <Th>{t.sales.description}</Th>
                <Th numeric>{t.sales.quantity}</Th>
                <Th numeric>{t.sales.unitPrice}</Th>
                <Th numeric>{t.sales.lineTotal}</Th>
              </tr>
            </thead>
            <tbody>
              {invoice.data.lines.map((line) => (
                <tr key={line.id}>
                  <Td>
                    {line.description}
                    {line.mxik_code ? (
                      <span className="block font-mono text-[10px] text-[var(--text-muted)]">
                        {t.sales.mxikCode}: {line.mxik_code}
                      </span>
                    ) : null}
                  </Td>
                  <Td numeric>{line.quantity}</Td>
                  <Td numeric>{line.unit_price}</Td>
                  <Td numeric>{line.line_total}</Td>
                </tr>
              ))}
              <tr>
                <Td className="font-semibold">{t.sales.total}</Td>
                <Td />
                <Td />
                <Td numeric className="font-semibold">
                  {invoice.data.total}
                </Td>
              </tr>
            </tbody>
          </Table>
        </div>
      ) : null}
    </Dialog>
  )
}

function VoidDialog({
  invoice,
  onClose,
  onDone,
  onError,
}: {
  invoice: Invoice | null
  onClose: () => void
  onDone: (message: string) => void
  onError: (message: string) => void
}) {
  const { t } = useI18n()
  const [reason, setReason] = useState('')

  const act = useMutation({
    mutationFn: () =>
      api.post<InvoiceDetail>(`/sales/invoices/${invoice?.id}/void`, { reason }),
    onSuccess: () => {
      setReason('')
      onDone(t.sales.voided)
    },
    onError: (err) => onError(describeError(err, t)),
  })

  return (
    <Dialog
      open={invoice !== null}
      onClose={onClose}
      title={`${t.sales.voidTitle}: ${invoice?.number ?? ''}`}
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
            {t.sales.voidInvoice}
          </Button>
        </>
      }
    >
      <p className="mb-4 text-sm text-[var(--text-secondary)]">{t.sales.voidBody}</p>
      <Field label={t.sales.reason} htmlFor="void-reason" required>
        <Input
          id="void-reason"
          value={reason}
          onChange={(event) => setReason(event.target.value)}
        />
      </Field>
    </Dialog>
  )
}
