import { useState } from 'react'
import { useI18n } from '@/i18n'
import { useInventoryItems, useStockLocations, useStockMovements } from '@/lib/queries'
import { describeError, errorRequestId } from '@/lib/errorMessage'
import {
  Badge,
  Card,
  CardHeader,
  EmptyState,
  ErrorState,
  LoadingState,
  Select,
  Table,
  Td,
  Th,
} from '@/components/ui'
import { displayQuantity, quantityDirection } from '@/lib/inventory'
import type { StockMovementKind } from '@/lib/types'

const KINDS: StockMovementKind[] = [
  'RECEIPT',
  'CONSUMPTION',
  'ADJUSTMENT',
  'WRITE_OFF',
  'TRANSFER_IN',
  'TRANSFER_OUT',
]

/**
 * Every movement, newest first — the ledger the balances are summed from.
 *
 * Read-only by design. A movement is never edited or deleted: a mistake is
 * corrected by an approved count, which leaves both the error and the
 * correction on the record.
 */
export function MovementsPanel() {
  const { t } = useI18n()
  const [itemId, setItemId] = useState('')
  const [locationId, setLocationId] = useState('')
  const [kind, setKind] = useState('')

  const items = useInventoryItems({}, true)
  const locations = useStockLocations(true)
  const movements = useStockMovements(
    {
      item_id: itemId || undefined,
      location_id: locationId || undefined,
      kind: kind || undefined,
      limit: 200,
    },
    true,
  )

  const itemName = (id: string) =>
    (items.data ?? []).find((row) => row.id === id)?.name ?? id.slice(0, 8)
  const itemUnit = (id: string) => (items.data ?? []).find((row) => row.id === id)?.unit ?? ''
  const locationName = (id: string) =>
    (locations.data ?? []).find((row) => row.id === id)?.name ?? '—'

  const kindLabel = (value: StockMovementKind) => t.inventory.kinds[value]
  const sourceLabel = (value: string) =>
    t.inventory.sources[value as keyof typeof t.inventory.sources] ?? value

  return (
    <Card>
      <CardHeader
        title={t.inventory.movementsTab}
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <label className="sr-only" htmlFor="movement-item">
              {t.inventory.material}
            </label>
            <Select
              id="movement-item"
              value={itemId}
              onChange={(event) => setItemId(event.target.value)}
              className="w-48"
            >
              <option value="">{t.inventory.allMaterials}</option>
              {(items.data ?? []).map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name}
                </option>
              ))}
            </Select>
            <label className="sr-only" htmlFor="movement-location">
              {t.inventory.location}
            </label>
            <Select
              id="movement-location"
              value={locationId}
              onChange={(event) => setLocationId(event.target.value)}
              className="w-44"
            >
              <option value="">{t.inventory.allLocations}</option>
              {(locations.data ?? []).map((location) => (
                <option key={location.id} value={location.id}>
                  {location.name}
                </option>
              ))}
            </Select>
            <label className="sr-only" htmlFor="movement-kind">
              {t.inventory.kind}
            </label>
            <Select
              id="movement-kind"
              value={kind}
              onChange={(event) => setKind(event.target.value)}
              className="w-44"
            >
              <option value="">{t.inventory.allKinds}</option>
              {KINDS.map((value) => (
                <option key={value} value={value}>
                  {kindLabel(value)}
                </option>
              ))}
            </Select>
          </div>
        }
      />
      {movements.isLoading ? (
        <LoadingState label={t.common.loading} />
      ) : movements.isError ? (
        <ErrorState
          title={t.errors.loadFailedTitle}
          message={describeError(movements.error, t)}
          requestId={errorRequestId(movements.error)}
          requestIdLabel={t.common.requestId}
          onRetry={() => void movements.refetch()}
          retryLabel={t.common.retry}
        />
      ) : (movements.data ?? []).length === 0 ? (
        <EmptyState title={t.inventory.noMovements} body={t.inventory.stockArrivesHow} />
      ) : (
        <Table>
          <thead>
            <tr>
              <Th>{t.inventory.date}</Th>
              <Th>{t.inventory.material}</Th>
              <Th>{t.inventory.location}</Th>
              <Th>{t.inventory.kind}</Th>
              <Th numeric>{t.inventory.quantity}</Th>
              <Th>{t.inventory.source}</Th>
            </tr>
          </thead>
          <tbody>
            {(movements.data ?? []).map((movement) => {
              const direction = quantityDirection(movement.quantity)
              return (
                <tr key={movement.id}>
                  <Td>{movement.occurred_on}</Td>
                  <Td>{itemName(movement.item_id)}</Td>
                  <Td>{locationName(movement.location_id)}</Td>
                  <Td>
                    <Badge tone={direction === 'in' ? 'success' : 'warning'}>
                      {kindLabel(movement.kind)}
                    </Badge>
                  </Td>
                  <Td numeric>
                    {displayQuantity(movement.quantity)} {itemUnit(movement.item_id)}
                  </Td>
                  <Td>
                    {sourceLabel(movement.source_kind)}
                    {movement.reason ? (
                      <span className="block text-xs text-[var(--text-muted)]">
                        {movement.reason}
                      </span>
                    ) : null}
                  </Td>
                </tr>
              )
            })}
          </tbody>
        </Table>
      )}
    </Card>
  )
}
