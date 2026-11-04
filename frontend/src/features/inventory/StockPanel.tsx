import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useI18n } from '@/i18n'
import { useAuth } from '@/features/auth/AuthProvider'
import { api } from '@/lib/api'
import {
  useExpiringStock,
  useInventoryItems,
  useLowStock,
  useStockBalances,
  useStockBatches,
  useStockLocations,
  useStockValuation,
} from '@/lib/queries'
import { describeError, errorRequestId } from '@/lib/errorMessage'
import {
  Alert,
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
import {
  compareQuantities,
  displayQuantity,
  expiryUrgency,
  groupByItem,
  parseQuantityInput,
  sortByUrgency,
  wouldGoNegative,
} from '@/lib/inventory'
import type { InventoryItem } from '@/lib/types'

/**
 * What is on the shelves, what is running out, and what is about to expire.
 *
 * Every figure is the server's sum over the movement ledger, fetched fresh
 * rather than cached: a stale balance is how somebody promises a material
 * the other surgery has already used.
 */
export function StockPanel() {
  const { t } = useI18n()
  const { can } = useAuth()
  const mayWriteOff = can('inventory.adjustment.approve')
  const mayTransfer = can('inventory.item.manage')

  const [locationFilter, setLocationFilter] = useState('')
  const [writingOff, setWritingOff] = useState(false)
  const [transferring, setTransferring] = useState(false)

  const locations = useStockLocations(true)
  const balances = useStockBalances(
    { location_id: locationFilter || undefined },
    true,
  )
  const low = useLowStock(true)
  const expiring = useExpiringStock(90, true)
  const valuation = useStockValuation(true)

  const grouped = groupByItem(balances.data ?? [])
  const locationName = (id: string) =>
    (locations.data ?? []).find((location) => location.id === id)?.name ?? '—'

  return (
    <div className="space-y-5">
      {(low.data ?? []).length > 0 ? (
        <Alert tone="warning">
          <strong>{t.inventory.lowStockTitle}</strong>{' '}
          {(low.data ?? [])
            .map((row) => `${row.name} (${displayQuantity(row.on_hand)} ${row.unit})`)
            .join(' · ')}
        </Alert>
      ) : null}

      {(expiring.data ?? []).some((row) => expiryUrgency(row.days_remaining) !== 'watch') ? (
        <Alert tone="warning">
          <strong>{t.inventory.expiringTitle}</strong>{' '}
          {sortByUrgency(expiring.data ?? [])
            .filter((row) => expiryUrgency(row.days_remaining) !== 'watch')
            .map((row) =>
              row.days_remaining < 0
                ? `${row.item_name} ${row.batch_code} — ${t.inventory.expired}`
                : `${row.item_name} ${row.batch_code} — ${row.days_remaining} ${t.inventory.days}`,
            )
            .join(' · ')}
        </Alert>
      ) : null}

      <Card>
        <CardHeader
          title={t.inventory.onHand}
          actions={
            <div className="flex flex-wrap items-center gap-2">
              <label className="sr-only" htmlFor="stock-location">
                {t.inventory.location}
              </label>
              <Select
                id="stock-location"
                value={locationFilter}
                onChange={(event) => setLocationFilter(event.target.value)}
                className="w-48"
              >
                <option value="">{t.inventory.allLocations}</option>
                {(locations.data ?? []).map((location) => (
                  <option key={location.id} value={location.id}>
                    {location.name}
                  </option>
                ))}
              </Select>
              {mayTransfer ? (
                <Button size="sm" onClick={() => setTransferring(true)}>
                  {t.inventory.transfer}
                </Button>
              ) : null}
              {mayWriteOff ? (
                <Button size="sm" variant="danger" onClick={() => setWritingOff(true)}>
                  {t.inventory.writeOff}
                </Button>
              ) : null}
            </div>
          }
        />
        {balances.isLoading ? (
          <LoadingState label={t.common.loading} />
        ) : balances.isError ? (
          <ErrorState
            title={t.errors.loadFailedTitle}
            message={describeError(balances.error, t)}
            requestId={errorRequestId(balances.error)}
            requestIdLabel={t.common.requestId}
            onRetry={() => void balances.refetch()}
            retryLabel={t.common.retry}
          />
        ) : (balances.data ?? []).length === 0 ? (
          <EmptyState title={t.inventory.nothingOnTheShelf} body={t.inventory.stockArrivesHow} />
        ) : (
          <Table>
            <thead>
              <tr>
                <Th>{t.inventory.material}</Th>
                <Th>{t.inventory.location}</Th>
                <Th>{t.inventory.batch}</Th>
                <Th numeric>{t.inventory.quantity}</Th>
              </tr>
            </thead>
            <tbody>
              {(balances.data ?? []).map((row) => (
                <tr key={`${row.item_id}-${row.location_id}-${row.batch_id ?? 'none'}`}>
                  <Td>
                    {row.item_name}
                    <span className="ml-2 font-mono text-xs text-[var(--text-muted)]">
                      {row.item_code}
                    </span>
                  </Td>
                  <Td>{locationName(row.location_id)}</Td>
                  <Td>
                    {row.batch_code ? (
                      <>
                        <span className="font-mono text-xs">{row.batch_code}</span>
                        {row.expires_on ? (
                          <span className="ml-2 text-xs text-[var(--text-muted)]">
                            {row.expires_on}
                          </span>
                        ) : null}
                      </>
                    ) : (
                      '—'
                    )}
                  </Td>
                  <Td numeric>
                    {displayQuantity(row.quantity)} {row.unit}
                  </Td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </Card>

      {grouped.length > 0 ? (
        <Card>
          <CardHeader title={t.inventory.byMaterial} />
          <Table>
            <thead>
              <tr>
                <Th>{t.inventory.material}</Th>
                <Th numeric>{t.inventory.quantity}</Th>
                <Th numeric>{t.inventory.shelves}</Th>
              </tr>
            </thead>
            <tbody>
              {grouped.map((row) => (
                <tr key={row.itemId}>
                  <Td>{row.name}</Td>
                  <Td numeric>
                    {displayQuantity(row.quantity)} {row.unit}
                  </Td>
                  <Td numeric>{row.locations}</Td>
                </tr>
              ))}
            </tbody>
          </Table>
        </Card>
      ) : null}

      <ValuationCard valuation={valuation} />

      {writingOff ? <WriteOffDialog onClose={() => setWritingOff(false)} /> : null}
      {transferring ? <TransferDialog onClose={() => setTransferring(false)} /> : null}
    </div>
  )
}

/**
 * What the stock is worth, or a plain statement that nobody has said how to
 * decide — which is the honest answer while OPEN_QUESTIONS I1 is unanswered.
 */
function ValuationCard({
  valuation,
}: {
  valuation: ReturnType<typeof useStockValuation>
}) {
  const { t, formatMoney } = useI18n()
  if (valuation.isLoading || valuation.isError || !valuation.data) return null
  const { method, total_value, rows } = valuation.data

  return (
    <Card>
      <CardHeader title={t.inventory.valuationTitle} />
      {method === 'UNDECLARED' ? (
        <div className="px-4 pb-4">
          <Alert tone="info">{t.inventory.valuationUndeclared}</Alert>
        </div>
      ) : (
        <Table>
          <thead>
            <tr>
              <Th>{t.inventory.material}</Th>
              <Th numeric>{t.inventory.quantity}</Th>
              <Th numeric>{t.inventory.unitCost}</Th>
              <Th numeric>{t.inventory.value}</Th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.item_id}>
                <Td>{row.name}</Td>
                <Td numeric>
                  {displayQuantity(row.quantity)} {row.unit}
                </Td>
                <Td numeric>
                  {row.unit_cost === null ? (
                    <span className="text-[var(--text-muted)]">{t.inventory.noCostYet}</span>
                  ) : (
                    formatMoney(row.unit_cost)
                  )}
                </Td>
                <Td numeric>
                  {row.value === null ? '—' : formatMoney(row.value)}
                </Td>
              </tr>
            ))}
            {total_value !== null ? (
              <tr>
                <Td colSpan={3}>
                  <strong>{t.inventory.totalValue}</strong>
                </Td>
                <Td numeric>
                  <strong>{formatMoney(total_value)}</strong>
                </Td>
              </tr>
            ) : null}
          </tbody>
        </Table>
      )}
    </Card>
  )
}

/** Shared picker: a material, a shelf, a lot and a quantity. */
function useStockForm() {
  const [itemId, setItemId] = useState('')
  const [locationId, setLocationId] = useState('')
  const [batchId, setBatchId] = useState('')
  const [quantity, setQuantity] = useState('')

  const items = useInventoryItems({}, true)
  const locations = useStockLocations(true)
  const batches = useStockBatches(itemId || null, Boolean(itemId))
  const balances = useStockBalances(
    { item_id: itemId || undefined, location_id: locationId || undefined },
    Boolean(itemId),
  )

  const item: InventoryItem | undefined = (items.data ?? []).find((row) => row.id === itemId)
  const available = (balances.data ?? [])
    .filter((row) => (batchId ? row.batch_id === batchId : true))
    .reduce((running, row) => (compareQuantities(row.quantity, '0') > 0 ? row.quantity : running), '0')

  return {
    itemId,
    setItemId,
    locationId,
    setLocationId,
    batchId,
    setBatchId,
    quantity,
    setQuantity,
    items,
    locations,
    batches,
    item,
    available,
  }
}

function WriteOffDialog({ onClose }: { onClose: () => void }) {
  const { t } = useI18n()
  const queryClient = useQueryClient()
  const form = useStockForm()
  const [reason, setReason] = useState('')
  const [error, setError] = useState<string | null>(null)

  const parsed = parseQuantityInput(form.quantity)
  const short = parsed !== null && form.quantity !== '' && wouldGoNegative(form.available, form.quantity)

  const mutation = useMutation({
    mutationFn: () =>
      api.post('/inventory/write-offs', {
        item_id: form.itemId,
        quantity: form.quantity,
        reason,
        location_id: form.locationId || undefined,
        batch_id: form.batchId || undefined,
      }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['inventory'] })
      onClose()
    },
    onError: (err) => setError(describeError(err, t)),
  })

  return (
    <Dialog open closeLabel={t.common.close} title={t.inventory.writeOff} onClose={onClose}>
      <form
        className="space-y-3"
        onSubmit={(event) => {
          event.preventDefault()
          setError(null)
          mutation.mutate()
        }}
      >
        <MaterialPicker form={form} />
        <Field label={t.inventory.quantity} htmlFor="write-off-quantity">
          <Input
            id="write-off-quantity"
            value={form.quantity}
            inputMode="decimal"
            onChange={(event) => form.setQuantity(event.target.value)}
            required
          />
        </Field>
        {short ? <Alert tone="warning">{t.inventory.wouldGoShort}</Alert> : null}
        <Field label={t.inventory.reason} htmlFor="write-off-reason">
          <Textarea
            id="write-off-reason"
            value={reason}
            onChange={(event) => setReason(event.target.value)}
            required
          />
        </Field>
        <Alert tone="info">{t.inventory.writeOffAudited}</Alert>
        {error ? <Alert tone="danger">{error}</Alert> : null}
        <div className="flex justify-end gap-2">
          <Button type="button" onClick={onClose}>
            {t.common.cancel}
          </Button>
          <Button
            type="submit"
            variant="danger"
            disabled={mutation.isPending || !form.itemId || !reason.trim()}
          >
            {t.inventory.writeOff}
          </Button>
        </div>
      </form>
    </Dialog>
  )
}

function TransferDialog({ onClose }: { onClose: () => void }) {
  const { t } = useI18n()
  const queryClient = useQueryClient()
  const form = useStockForm()
  const [toLocationId, setToLocationId] = useState('')
  const [error, setError] = useState<string | null>(null)

  const mutation = useMutation({
    mutationFn: () =>
      api.post('/inventory/transfers', {
        item_id: form.itemId,
        from_location_id: form.locationId,
        to_location_id: toLocationId,
        quantity: form.quantity,
        batch_id: form.batchId || undefined,
      }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['inventory'] })
      onClose()
    },
    onError: (err) => setError(describeError(err, t)),
  })

  const sameShelf = Boolean(form.locationId) && form.locationId === toLocationId

  return (
    <Dialog open closeLabel={t.common.close} title={t.inventory.transfer} onClose={onClose}>
      <form
        className="space-y-3"
        onSubmit={(event) => {
          event.preventDefault()
          setError(null)
          mutation.mutate()
        }}
      >
        <MaterialPicker form={form} fromLabel={t.inventory.fromLocation} />
        <Field label={t.inventory.toLocation} htmlFor="transfer-to">
          <Select
            id="transfer-to"
            value={toLocationId}
            onChange={(event) => setToLocationId(event.target.value)}
            required
          >
            <option value="">{t.inventory.choose}</option>
            {(form.locations.data ?? []).map((location) => (
              <option key={location.id} value={location.id}>
                {location.name}
              </option>
            ))}
          </Select>
        </Field>
        <Field label={t.inventory.quantity} htmlFor="transfer-quantity">
          <Input
            id="transfer-quantity"
            value={form.quantity}
            inputMode="decimal"
            onChange={(event) => form.setQuantity(event.target.value)}
            required
          />
        </Field>
        {sameShelf ? <Alert tone="warning">{t.inventory.sameShelf}</Alert> : null}
        {error ? <Alert tone="danger">{error}</Alert> : null}
        <div className="flex justify-end gap-2">
          <Button type="button" onClick={onClose}>
            {t.common.cancel}
          </Button>
          <Button
            type="submit"
            variant="primary"
            disabled={mutation.isPending || !form.itemId || !toLocationId || sameShelf}
          >
            {t.inventory.transfer}
          </Button>
        </div>
      </form>
    </Dialog>
  )
}

function MaterialPicker({
  form,
  fromLabel,
}: {
  form: ReturnType<typeof useStockForm>
  fromLabel?: string
}) {
  const { t } = useI18n()
  return (
    <>
      <Field label={t.inventory.material} htmlFor="picker-item">
        <Select
          id="picker-item"
          value={form.itemId}
          onChange={(event) => {
            form.setItemId(event.target.value)
            form.setBatchId('')
          }}
          required
        >
          <option value="">{t.inventory.choose}</option>
          {(form.items.data ?? []).map((item) => (
            <option key={item.id} value={item.id}>
              {item.name} ({item.unit})
            </option>
          ))}
        </Select>
      </Field>
      <Field label={fromLabel ?? t.inventory.location} htmlFor="picker-location">
        <Select
          id="picker-location"
          value={form.locationId}
          onChange={(event) => form.setLocationId(event.target.value)}
          required={Boolean(fromLabel)}
        >
          <option value="">{fromLabel ? t.inventory.choose : t.inventory.defaultLocation}</option>
          {(form.locations.data ?? []).map((location) => (
            <option key={location.id} value={location.id}>
              {location.name}
            </option>
          ))}
        </Select>
      </Field>
      {form.item?.is_batch_tracked ? (
        <Field label={t.inventory.batch} htmlFor="picker-batch">
          <Select
            id="picker-batch"
            value={form.batchId}
            onChange={(event) => form.setBatchId(event.target.value)}
            required
          >
            <option value="">{t.inventory.choose}</option>
            {(form.batches.data ?? []).map((batch) => (
              <option key={batch.id} value={batch.id}>
                {batch.code}
                {batch.expires_on ? ` — ${batch.expires_on}` : ''}
              </option>
            ))}
          </Select>
        </Field>
      ) : null}
      {form.itemId ? (
        <p className="text-sm text-[var(--text-muted)]">
          {t.inventory.onShelfNow}: <strong>{displayQuantity(form.available)}</strong>{' '}
          {form.item?.unit ?? ''}
        </p>
      ) : null}
    </>
  )
}
