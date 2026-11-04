import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useI18n } from '@/i18n'
import { useAuth } from '@/features/auth/AuthProvider'
import { api } from '@/lib/api'
import { usePurchaseOrder, usePurchaseOrders, useSuppliers } from '@/lib/queries'
import { describeError, errorRequestId } from '@/lib/errorMessage'
import {
  lineTotal,
  nextOrderStatuses,
  orderAwaitsDelivery,
  orderIsFinished,
  orderMoveNeedsReason,
  sumAmounts,
} from '@/lib/purchasing'
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
import type { OrderStatus, PurchaseOrder } from '@/lib/types'

const ORDER_STATUSES: readonly OrderStatus[] = [
  'DRAFT',
  'APPROVED',
  'SENT',
  'PARTIALLY_RECEIVED',
  'RECEIVED',
  'CANCELLED',
  'CLOSED',
]

/**
 * Orders, and the deliveries against them.
 *
 * The notice at the top is not decoration: a person looking at an approved
 * order worth five million so'm reasonably assumes the books know about it,
 * and they do not. Saying so on the screen is cheaper than explaining it
 * afterwards to somebody reconciling a trial balance.
 */
export function OrdersPanel() {
  const { t, formatDate, formatMoney } = useI18n()
  const { can } = useAuth()
  const mayManage = can('purchasing.order.manage')

  const [status, setStatus] = useState('')
  const [openId, setOpenId] = useState<string | null>(null)
  const [creating, setCreating] = useState(false)

  const orders = usePurchaseOrders({ status: status || undefined }, true)

  if (openId) {
    return <OrderDetail orderId={openId} onBack={() => setOpenId(null)} />
  }

  return (
    <>
      <Alert tone="info">{t.purchasing.postsNothingNotice}</Alert>

      <div className="mt-4">
        <Card>
          <CardHeader
            title={t.purchasing.ordersTab}
            actions={
              <div className="flex flex-wrap items-center gap-2">
                <label className="sr-only" htmlFor="order-status">
                  {t.purchasing.orderStatus}
                </label>
                <Select
                  id="order-status"
                  value={status}
                  onChange={(event) => setStatus(event.target.value)}
                  className="w-auto"
                >
                  <option value="">{t.common.all}</option>
                  {ORDER_STATUSES.map((value) => (
                    <option key={value} value={value}>
                      {t.purchasing[`order${value}` as const]}
                    </option>
                  ))}
                </Select>
                {mayManage ? (
                  <Button size="sm" variant="primary" onClick={() => setCreating(true)}>
                    {t.purchasing.newOrder}
                  </Button>
                ) : null}
              </div>
            }
          />
          {orders.isLoading ? (
            <LoadingState label={t.common.loading} />
          ) : orders.isError ? (
            <ErrorState
              title={t.errors.loadFailedTitle}
              message={describeError(orders.error, t)}
              requestId={errorRequestId(orders.error)}
              requestIdLabel={t.common.requestId}
              onRetry={() => void orders.refetch()}
              retryLabel={t.common.retry}
            />
          ) : (orders.data ?? []).length === 0 ? (
            <EmptyState title={t.purchasing.noOrders} />
          ) : (
            <Table>
              <thead>
                <tr>
                  <Th>{t.purchasing.reference}</Th>
                  <Th>{t.purchasing.supplier}</Th>
                  <Th>{t.purchasing.orderedOn}</Th>
                  <Th numeric>{t.purchasing.orderTotal}</Th>
                  <Th>{t.purchasing.orderStatus}</Th>
                  <Th>{t.common.actions}</Th>
                </tr>
              </thead>
              <tbody>
                {(orders.data ?? []).map((order) => (
                  <tr key={order.id}>
                    <Td>
                      <span className="font-mono text-xs">{order.code}</span>
                    </Td>
                    <Td>{order.supplier_name}</Td>
                    <Td>{formatDate(order.ordered_on)}</Td>
                    <Td numeric>{formatMoney(order.total, order.currency)}</Td>
                    <Td>
                      <OrderBadge status={order.status} />
                    </Td>
                    <Td>
                      <div className="flex justify-end">
                        <Button size="sm" onClick={() => setOpenId(order.id)}>
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
      </div>

      {creating ? (
        <NewOrderDialog
          onClose={() => setCreating(false)}
          onCreated={(order) => {
            setCreating(false)
            setOpenId(order.id)
          }}
        />
      ) : null}
    </>
  )
}

function OrderBadge({ status }: { status: OrderStatus }) {
  const { t } = useI18n()
  const tone: Record<OrderStatus, 'neutral' | 'success' | 'warning' | 'brand'> = {
    DRAFT: 'neutral',
    APPROVED: 'brand',
    SENT: 'brand',
    PARTIALLY_RECEIVED: 'warning',
    RECEIVED: 'success',
    CANCELLED: 'neutral',
    CLOSED: 'neutral',
  }
  return (
    <Badge tone={tone[status]}>{t.purchasing[`order${status}` as const]}</Badge>
  )
}

// --------------------------------------------------------------- one order

function OrderDetail({ orderId, onBack }: { orderId: string; onBack: () => void }) {
  const { t, formatDate, formatMoney } = useI18n()
  const queryClient = useQueryClient()
  const { can } = useAuth()
  const order = usePurchaseOrder(orderId, true)
  const [error, setError] = useState<string | null>(null)
  const [closing, setClosing] = useState(false)
  const [reason, setReason] = useState('')
  const [receiving, setReceiving] = useState(false)

  const mayManage = can('purchasing.order.manage')
  const mayApprove = can('purchasing.order.approve')
  const mayReceive = can('inventory.receipt.record')

  const move = useMutation({
    mutationFn: (next: { status: OrderStatus; reason?: string }) =>
      api.post(`/purchasing/orders/${orderId}/status`, next),
    onSuccess: () => {
      setError(null)
      setClosing(false)
      setReason('')
      void queryClient.invalidateQueries({ queryKey: ['purchasing'] })
    },
    onError: (caught) => setError(describeError(caught, t)),
  })

  if (order.isLoading) return <LoadingState label={t.common.loading} />
  if (order.isError || !order.data) {
    return (
      <ErrorState
        title={t.errors.loadFailedTitle}
        message={describeError(order.error, t)}
        requestId={errorRequestId(order.error)}
        requestIdLabel={t.common.requestId}
        onRetry={() => void order.refetch()}
        retryLabel={t.common.retry}
      />
    )
  }

  const detail = order.data
  const stillToCome = detail.outstanding.filter((row) => Number(row.outstanding) > 0)

  return (
    <div className="space-y-4">
      <Button size="sm" onClick={onBack}>
        ← {t.purchasing.back}
      </Button>

      <Card>
        <CardHeader
          title={`${detail.code} · ${detail.supplier_name}`}
          actions={<OrderBadge status={detail.status} />}
        />
        <DescriptionList
          items={[
            { label: t.purchasing.orderedOn, value: formatDate(detail.ordered_on) },
            {
              label: t.purchasing.expectedOn,
              value: detail.expected_on ? formatDate(detail.expected_on) : '—',
            },
            {
              label: t.purchasing.orderTotal,
              value: formatMoney(detail.total, detail.currency),
            },
            ...(detail.notes
              ? [{ label: t.purchasing.notes, value: detail.notes }]
              : []),
            ...(detail.closed_reason
              ? [{ label: t.purchasing.closeReason, value: detail.closed_reason }]
              : []),
          ]}
        />

        <div className="space-y-3 px-4 pb-4">
          {detail.self_approved ? (
            <Alert tone="warning">{t.purchasing.selfApprovedNotice}</Alert>
          ) : null}
          {orderIsFinished(detail.status) ? (
            <Alert tone="info">{t.purchasing.orderFinished}</Alert>
          ) : null}

          <div className="flex flex-wrap gap-2">
            {nextOrderStatuses(detail.status).map((next) => {
              const allowed = next === 'APPROVED' ? mayApprove : mayManage
              if (!allowed) return null
              return (
                <Button
                  key={next}
                  size="sm"
                  variant={next === 'CANCELLED' ? 'ghost' : 'primary'}
                  disabled={move.isPending}
                  onClick={() =>
                    orderMoveNeedsReason(next)
                      ? setClosing(true)
                      : move.mutate({ status: next })
                  }
                >
                  {t.purchasing[`to${next}` as 'toAPPROVED']}
                </Button>
              )
            })}
            {mayReceive && orderAwaitsDelivery(detail.status) ? (
              <Button size="sm" onClick={() => setReceiving(true)}>
                {t.purchasing.receive}
              </Button>
            ) : null}
          </div>

          {error ? <Alert tone="danger">{error}</Alert> : null}
        </div>
      </Card>

      <Card>
        <CardHeader title={t.purchasing.ordersTab} />
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

      <Card>
        <CardHeader title={t.purchasing.outstandingHeading} />
        {stillToCome.length === 0 ? (
          <EmptyState title={t.purchasing.allArrived} />
        ) : (
          <Table>
            <thead>
              <tr>
                <Th>{t.purchasing.description}</Th>
                <Th numeric>{t.purchasing.ordered}</Th>
                <Th numeric>{t.purchasing.received}</Th>
                <Th numeric>{t.purchasing.stillToCome}</Th>
              </tr>
            </thead>
            <tbody>
              {stillToCome.map((row) => (
                <tr key={row.order_line_id}>
                  <Td>{row.description}</Td>
                  <Td numeric>{row.ordered}</Td>
                  <Td numeric>{row.received}</Td>
                  <Td numeric>{row.outstanding}</Td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </Card>

      <Dialog
        open={closing}
        onClose={() => setClosing(false)}
        title={t.purchasing.toCLOSED}
        closeLabel={t.common.close}
        footer={
          <>
            <Button onClick={() => setClosing(false)} disabled={move.isPending}>
              {t.common.cancel}
            </Button>
            <Button
              variant="primary"
              disabled={reason.trim().length < 3 || move.isPending}
              loading={move.isPending}
              onClick={() => move.mutate({ status: 'CLOSED', reason: reason.trim() })}
            >
              {t.purchasing.toCLOSED}
            </Button>
          </>
        }
      >
        <Field label={t.purchasing.closeReason} required htmlFor="close-reason">
          <Textarea
            id="close-reason"
            rows={2}
            value={reason}
            onChange={(event) => setReason(event.target.value)}
          />
        </Field>
      </Dialog>

      {receiving ? (
        <ReceiveDialog order={detail} onClose={() => setReceiving(false)} />
      ) : null}
    </div>
  )
}

// ------------------------------------------------------------------ dialogs

interface DraftLine {
  key: number
  description: string
  quantity: string
  unit: string
  unitPrice: string
}

function NewOrderDialog({
  onClose,
  onCreated,
}: {
  onClose: () => void
  onCreated: (order: PurchaseOrder) => void
}) {
  const { t, formatMoney } = useI18n()
  const queryClient = useQueryClient()
  const suppliers = useSuppliers({}, true)
  const [supplierId, setSupplierId] = useState('')
  const [orderedOn, setOrderedOn] = useState(todayInClinic())
  const [expectedOn, setExpectedOn] = useState('')
  const [rows, setRows] = useState<DraftLine[]>([
    { key: 1, description: '', quantity: '1', unit: '', unitPrice: '' },
  ])
  const [error, setError] = useState<string | null>(null)

  const usable = rows.filter(
    (row) => row.description.trim() !== '' && row.unitPrice.trim() !== '',
  )
  const total = sumAmounts(usable.map((row) => lineTotal(row.quantity, row.unitPrice)))

  const create = useMutation({
    mutationFn: () =>
      api.post<PurchaseOrder>('/purchasing/orders', {
        supplier_id: supplierId,
        ordered_on: orderedOn,
        expected_on: expectedOn || null,
        lines: usable.map((row) => ({
          description: row.description.trim(),
          quantity: row.quantity.trim(),
          unit_price: row.unitPrice.trim(),
          unit: row.unit.trim() || null,
        })),
      }),
    onSuccess: (order) => {
      void queryClient.invalidateQueries({ queryKey: ['purchasing'] })
      onCreated(order)
    },
    onError: (caught) => setError(describeError(caught, t)),
  })

  return (
    <Dialog
      open
      size="lg"
      onClose={onClose}
      title={t.purchasing.newOrder}
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
        <p className="text-sm text-[var(--text-secondary)]">
          {t.purchasing.newOrderBody}
        </p>

        <div className="grid gap-3 sm:grid-cols-3">
          <Field label={t.purchasing.supplier} required htmlFor="order-supplier">
            <Select
              id="order-supplier"
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
          <Field label={t.purchasing.orderedOn} htmlFor="order-date">
            <Input
              id="order-date"
              type="date"
              value={orderedOn}
              onChange={(event) => setOrderedOn(event.target.value)}
            />
          </Field>
          <Field label={t.purchasing.expectedOn} htmlFor="order-expected">
            <Input
              id="order-expected"
              type="date"
              value={expectedOn}
              onChange={(event) => setExpectedOn(event.target.value)}
            />
          </Field>
        </div>

        <div className="space-y-2">
          {rows.map((row) => (
            <div
              key={row.key}
              className="grid gap-2 rounded-lg border border-[var(--border-subtle)] p-3 sm:grid-cols-[2fr_5rem_4rem_7rem]"
            >
              <Field
                label={t.purchasing.description}
                htmlFor={`order-line-desc-${row.key}`}
              >
                <Input
                  id={`order-line-desc-${row.key}`}
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
                htmlFor={`order-line-qty-${row.key}`}
              >
                <Input
                  id={`order-line-qty-${row.key}`}
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
              <Field label={t.purchasing.unit} htmlFor={`order-line-unit-${row.key}`}>
                <Input
                  id={`order-line-unit-${row.key}`}
                  value={row.unit}
                  onChange={(event) =>
                    setRows((current) =>
                      current.map((r) =>
                        r.key === row.key ? { ...r, unit: event.target.value } : r,
                      ),
                    )
                  }
                />
              </Field>
              <Field
                label={t.purchasing.unitPrice}
                htmlFor={`order-line-price-${row.key}`}
                hint={
                  row.unitPrice.trim() === ''
                    ? undefined
                    : formatMoney(lineTotal(row.quantity, row.unitPrice))
                }
              >
                <Input
                  id={`order-line-price-${row.key}`}
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
                  unit: '',
                  unitPrice: '',
                },
              ])
            }
          >
            {t.purchasing.addLine}
          </Button>
          <p className="text-sm">
            {t.purchasing.orderTotal}:{' '}
            <span className="font-medium">{formatMoney(total)}</span>
          </p>
        </div>

        {error ? <Alert tone="danger">{error}</Alert> : null}
      </div>
    </Dialog>
  )
}

interface DraftReceiptLine {
  orderLineId: string
  description: string
  quantity: string
  unit: string | null
}

function ReceiveDialog({
  order,
  onClose,
}: {
  order: { id: string; supplier_id: string; outstanding: { order_line_id: string; description: string; outstanding: string; unit: string | null }[] }
  onClose: () => void
}) {
  const { t } = useI18n()
  const queryClient = useQueryClient()
  const [receivedOn, setReceivedOn] = useState(todayInClinic())
  const [deliveryNote, setDeliveryNote] = useState('')
  const [rows, setRows] = useState<DraftReceiptLine[]>(() =>
    order.outstanding
      .filter((row) => Number(row.outstanding) > 0)
      .map((row) => ({
        orderLineId: row.order_line_id,
        description: row.description,
        // Pre-filled with everything still to come, which is what usually
        // arrives — and editable, because the rest of the time it is not.
        quantity: row.outstanding,
        unit: row.unit,
      })),
  )
  const [error, setError] = useState<string | null>(null)

  const usable = rows.filter((row) => Number(row.quantity) > 0)

  const receive = useMutation({
    mutationFn: () =>
      api.post('/purchasing/receipts', {
        supplier_id: order.supplier_id,
        order_id: order.id,
        received_on: receivedOn,
        delivery_note: deliveryNote.trim() || null,
        lines: usable.map((row) => ({
          description: row.description,
          quantity: row.quantity,
          order_line_id: row.orderLineId,
          unit: row.unit,
        })),
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
      size="lg"
      onClose={onClose}
      title={t.purchasing.receive}
      closeLabel={t.common.close}
      footer={
        <>
          <Button onClick={onClose} disabled={receive.isPending}>
            {t.common.cancel}
          </Button>
          <Button
            variant="primary"
            disabled={usable.length === 0 || receive.isPending}
            loading={receive.isPending}
            onClick={() => receive.mutate()}
          >
            {t.common.save}
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <p className="text-sm text-[var(--text-secondary)]">
          {t.purchasing.receiveBody}
        </p>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label={t.purchasing.receivedOn} htmlFor="receipt-date">
            <Input
              id="receipt-date"
              type="date"
              value={receivedOn}
              onChange={(event) => setReceivedOn(event.target.value)}
            />
          </Field>
          <Field label={t.purchasing.deliveryNote} htmlFor="receipt-note">
            <Input
              id="receipt-note"
              value={deliveryNote}
              onChange={(event) => setDeliveryNote(event.target.value)}
            />
          </Field>
        </div>

        {rows.length === 0 ? (
          <Alert tone="info">{t.purchasing.allArrived}</Alert>
        ) : (
          <div className="space-y-2">
            {rows.map((row) => (
              <div
                key={row.orderLineId}
                className="grid gap-2 rounded-lg border border-[var(--border-subtle)] p-3 sm:grid-cols-[2fr_6rem]"
              >
                <p className="self-center text-sm">{row.description}</p>
                <Field
                  label={t.purchasing.quantity}
                  htmlFor={`receive-qty-${row.orderLineId}`}
                >
                  <Input
                    id={`receive-qty-${row.orderLineId}`}
                    inputMode="decimal"
                    value={row.quantity}
                    onChange={(event) =>
                      setRows((current) =>
                        current.map((r) =>
                          r.orderLineId === row.orderLineId
                            ? { ...r, quantity: event.target.value }
                            : r,
                        ),
                      )
                    }
                  />
                </Field>
              </div>
            ))}
          </div>
        )}

        {error ? <Alert tone="danger">{error}</Alert> : null}
      </div>
    </Dialog>
  )
}
