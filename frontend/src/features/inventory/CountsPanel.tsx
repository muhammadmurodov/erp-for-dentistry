import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useI18n } from '@/i18n'
import { useAuth } from '@/features/auth/AuthProvider'
import { api } from '@/lib/api'
import {
  useInventoryItems,
  useStockBalances,
  useStockCount,
  useStockCounts,
  useStockLocations,
} from '@/lib/queries'
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
  Textarea,
  Th,
} from '@/components/ui'
import {
  countAgrees,
  countDifference,
  displayQuantity,
  mayApproveCount,
  nextCountStatuses,
} from '@/lib/inventory'
import type { AdjustmentStatus, StockCount } from '@/lib/types'

/**
 * The physical count: what was on the shelf, against what the ledger said.
 *
 * Two acts, deliberately kept apart. Whoever counts proposes; somebody else
 * agrees the difference, and only then does stock move. The approve button
 * is not offered to the person who counted — the server refuses it anyway,
 * but a button that will be refused is a button that should not be there.
 */
export function CountsPanel() {
  const { t } = useI18n()
  const { can, user } = useAuth()
  const mayCount = can('inventory.item.manage')
  const mayApprove = can('inventory.adjustment.approve')

  const [counting, setCounting] = useState(false)
  const [openId, setOpenId] = useState<string | null>(null)

  const counts = useStockCounts({}, true)
  const locations = useStockLocations(true)

  const locationName = (id: string) =>
    (locations.data ?? []).find((row) => row.id === id)?.name ?? '—'

  if (openId) {
    return (
      <CountDetail
        countId={openId}
        onBack={() => setOpenId(null)}
        mayApprove={mayApprove}
        viewerId={user?.id ?? null}
      />
    )
  }

  return (
    <>
      <Card>
        <CardHeader
          title={t.inventory.countsTab}
          actions={
            mayCount ? (
              <Button size="sm" variant="primary" onClick={() => setCounting(true)}>
                {t.inventory.newCount}
              </Button>
            ) : null
          }
        />
        {counts.isLoading ? (
          <LoadingState label={t.common.loading} />
        ) : counts.isError ? (
          <ErrorState
            title={t.errors.loadFailedTitle}
            message={describeError(counts.error, t)}
            requestId={errorRequestId(counts.error)}
            requestIdLabel={t.common.requestId}
            onRetry={() => void counts.refetch()}
            retryLabel={t.common.retry}
          />
        ) : (counts.data ?? []).length === 0 ? (
          <EmptyState title={t.inventory.noCounts} body={t.inventory.noCountsBody} />
        ) : (
          <Table>
            <thead>
              <tr>
                <Th>{t.inventory.reference}</Th>
                <Th>{t.inventory.date}</Th>
                <Th>{t.inventory.location}</Th>
                <Th>{t.inventory.status}</Th>
                <Th>{t.common.actions}</Th>
              </tr>
            </thead>
            <tbody>
              {(counts.data ?? []).map((count) => (
                <tr key={count.id}>
                  <Td>
                    <span className="font-mono text-xs">{count.code}</span>
                  </Td>
                  <Td>{count.counted_on}</Td>
                  <Td>{locationName(count.location_id)}</Td>
                  <Td>
                    <StatusBadge status={count.status} />
                  </Td>
                  <Td>
                    <div className="flex justify-end">
                      <Button size="sm" onClick={() => setOpenId(count.id)}>
                        {t.inventory.open}
                      </Button>
                    </div>
                  </Td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </Card>
      {counting ? <NewCountDialog onClose={() => setCounting(false)} /> : null}
    </>
  )
}

function StatusBadge({ status }: { status: AdjustmentStatus }) {
  const { t } = useI18n()
  const tone =
    status === 'APPROVED'
      ? 'success'
      : status === 'REJECTED'
        ? 'danger'
        : status === 'PROPOSED'
          ? 'warning'
          : 'neutral'
  return <Badge tone={tone}>{t.inventory.statuses[status]}</Badge>
}

function CountDetail({
  countId,
  onBack,
  mayApprove,
  viewerId,
}: {
  countId: string
  onBack: () => void
  mayApprove: boolean
  viewerId: string | null
}) {
  const { t } = useI18n()
  const queryClient = useQueryClient()
  const count = useStockCount(countId, true)
  const items = useInventoryItems({}, true)
  const [error, setError] = useState<string | null>(null)
  const [rejecting, setRejecting] = useState(false)

  const move = useMutation({
    mutationFn: (action: 'propose' | 'approve') =>
      api.post(`/inventory/counts/${countId}/${action}`, {}),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['inventory'] })
    },
    onError: (err) => setError(describeError(err, t)),
  })

  if (count.isLoading) return <LoadingState label={t.common.loading} />
  if (count.isError || !count.data) {
    return (
      <ErrorState
        title={t.errors.loadFailedTitle}
        message={describeError(count.error, t)}
        requestId={errorRequestId(count.error)}
        requestIdLabel={t.common.requestId}
        onRetry={() => void count.refetch()}
        retryLabel={t.common.retry}
      />
    )
  }

  const detail = count.data
  const itemName = (id: string) =>
    (items.data ?? []).find((row) => row.id === id)?.name ?? id.slice(0, 8)
  const canPropose = nextCountStatuses(detail.status).includes('PROPOSED')
  const canApprove = mayApproveCount(detail, viewerId, mayApprove)
  const countedItOneself =
    detail.status === 'PROPOSED' &&
    mayApprove &&
    detail.counted_by_user_id !== null &&
    detail.counted_by_user_id === viewerId

  return (
    <Card>
      <CardHeader
        title={`${t.inventory.count} ${detail.code}`}
        actions={
          <div className="flex flex-wrap gap-2">
            <Button size="sm" onClick={onBack}>
              {t.common.back}
            </Button>
            {canPropose ? (
              <Button
                size="sm"
                variant="primary"
                disabled={move.isPending}
                onClick={() => move.mutate('propose')}
              >
                {t.inventory.propose}
              </Button>
            ) : null}
            {canApprove ? (
              <Button
                size="sm"
                variant="primary"
                disabled={move.isPending}
                onClick={() => move.mutate('approve')}
              >
                {t.inventory.approve}
              </Button>
            ) : null}
            {detail.status === 'PROPOSED' && mayApprove ? (
              <Button size="sm" variant="danger" onClick={() => setRejecting(true)}>
                {t.inventory.reject}
              </Button>
            ) : null}
          </div>
        }
      />
      <div className="space-y-3 px-4 pb-4">
        <p className="text-sm text-[var(--text-muted)]">{detail.reason}</p>
        <StatusBadge status={detail.status} />
        {countedItOneself ? <Alert tone="info">{t.inventory.cannotApproveOwn}</Alert> : null}
        {detail.status === 'APPROVED' ? (
          <Alert tone="success">{t.inventory.approvedMoved}</Alert>
        ) : detail.status === 'REJECTED' ? (
          <Alert tone="warning">{detail.decision_note ?? t.inventory.rejected}</Alert>
        ) : (
          <Alert tone="info">{t.inventory.nothingMovedYet}</Alert>
        )}
        {error ? <Alert tone="danger">{error}</Alert> : null}
      </div>
      <Table>
        <thead>
          <tr>
            <Th>{t.inventory.material}</Th>
            <Th numeric>{t.inventory.systemSaid}</Th>
            <Th numeric>{t.inventory.counted}</Th>
            <Th numeric>{t.inventory.difference}</Th>
            <Th>{t.inventory.note}</Th>
          </tr>
        </thead>
        <tbody>
          {detail.lines.map((line) => {
            const agrees = countAgrees(line.system_quantity, line.counted_quantity)
            return (
              <tr key={line.id}>
                <Td>{itemName(line.item_id)}</Td>
                <Td numeric>{displayQuantity(line.system_quantity)}</Td>
                <Td numeric>{displayQuantity(line.counted_quantity)}</Td>
                <Td numeric>
                  {agrees ? (
                    <span className="text-[var(--text-muted)]">—</span>
                  ) : (
                    <strong>
                      {countDifference(line.system_quantity, line.counted_quantity)}
                    </strong>
                  )}
                </Td>
                <Td>{line.note ?? '—'}</Td>
              </tr>
            )
          })}
        </tbody>
      </Table>
      {rejecting ? (
        <RejectDialog countId={countId} onClose={() => setRejecting(false)} />
      ) : null}
    </Card>
  )
}

function RejectDialog({ countId, onClose }: { countId: string; onClose: () => void }) {
  const { t } = useI18n()
  const queryClient = useQueryClient()
  const [note, setNote] = useState('')
  const [error, setError] = useState<string | null>(null)

  const mutation = useMutation({
    mutationFn: () => api.post(`/inventory/counts/${countId}/reject`, { note }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['inventory'] })
      onClose()
    },
    onError: (err) => setError(describeError(err, t)),
  })

  return (
    <Dialog open closeLabel={t.common.close} title={t.inventory.reject} onClose={onClose}>
      <form
        className="space-y-3"
        onSubmit={(event) => {
          event.preventDefault()
          setError(null)
          mutation.mutate()
        }}
      >
        <Field label={t.inventory.whyRejected} htmlFor="reject-note">
          <Textarea
            id="reject-note"
            value={note}
            onChange={(event) => setNote(event.target.value)}
            required
          />
        </Field>
        {error ? <Alert tone="danger">{error}</Alert> : null}
        <div className="flex justify-end gap-2">
          <Button type="button" onClick={onClose}>
            {t.common.cancel}
          </Button>
          <Button type="submit" variant="danger" disabled={mutation.isPending || !note.trim()}>
            {t.inventory.reject}
          </Button>
        </div>
      </form>
    </Dialog>
  )
}

function NewCountDialog({ onClose }: { onClose: () => void }) {
  const { t } = useI18n()
  const queryClient = useQueryClient()
  const locations = useStockLocations(true)
  const [locationId, setLocationId] = useState('')
  const [reason, setReason] = useState('')
  const [counted, setCounted] = useState<Record<string, string>>({})
  const [error, setError] = useState<string | null>(null)

  const balances = useStockBalances(
    { location_id: locationId || undefined },
    Boolean(locationId),
  )
  const items = useInventoryItems({}, true)

  // Only what is on this shelf: counting a material that has never been
  // there is how a count sheet turns into a catalogue.
  const rows = (balances.data ?? []).filter((row) => row.batch_id === null)

  const mutation = useMutation({
    mutationFn: () =>
      api.post('/inventory/counts', {
        location_id: locationId,
        reason,
        lines: Object.entries(counted)
          .filter(([, value]) => value.trim() !== '')
          .map(([itemId, value]) => ({ item_id: itemId, counted_quantity: value })),
      }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['inventory'] })
      onClose()
    },
    onError: (err) => setError(describeError(err, t)),
  })

  const itemUnit = (id: string) => (items.data ?? []).find((row) => row.id === id)?.unit ?? ''
  const filled = Object.values(counted).filter((value) => value.trim() !== '').length

  return (
    <Dialog open closeLabel={t.common.close} title={t.inventory.newCount} onClose={onClose} size="lg">
      <form
        className="space-y-3"
        onSubmit={(event) => {
          event.preventDefault()
          setError(null)
          mutation.mutate()
        }}
      >
        <Field label={t.inventory.location} htmlFor="count-location">
          <Select
            id="count-location"
            value={locationId}
            onChange={(event) => setLocationId(event.target.value)}
            required
          >
            <option value="">{t.inventory.choose}</option>
            {(locations.data ?? []).map((location) => (
              <option key={location.id} value={location.id}>
                {location.name}
              </option>
            ))}
          </Select>
        </Field>
        <Field label={t.inventory.reason} htmlFor="count-reason">
          <Input
            id="count-reason"
            value={reason}
            onChange={(event) => setReason(event.target.value)}
            required
          />
        </Field>
        {locationId ? (
          rows.length === 0 ? (
            <EmptyState title={t.inventory.nothingOnTheShelf} />
          ) : (
            <Table>
              <thead>
                <tr>
                  <Th>{t.inventory.material}</Th>
                  <Th numeric>{t.inventory.systemSaid}</Th>
                  <Th numeric>{t.inventory.counted}</Th>
                  <Th numeric>{t.inventory.difference}</Th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => {
                  const value = counted[row.item_id] ?? ''
                  return (
                    <tr key={row.item_id}>
                      <Td>{row.item_name}</Td>
                      <Td numeric>
                        {displayQuantity(row.quantity)} {itemUnit(row.item_id)}
                      </Td>
                      <Td numeric>
                        <Input
                          value={value}
                          inputMode="decimal"
                          aria-label={`${row.item_name} ${t.inventory.counted}`}
                          onChange={(event) =>
                            setCounted((prev) => ({
                              ...prev,
                              [row.item_id]: event.target.value,
                            }))
                          }
                          className="w-24 text-right"
                        />
                      </Td>
                      <Td numeric>
                        {value.trim() === ''
                          ? '—'
                          : countDifference(row.quantity, value)}
                      </Td>
                    </tr>
                  )
                })}
              </tbody>
            </Table>
          )
        ) : null}
        <Alert tone="info">{t.inventory.countMovesNothing}</Alert>
        {error ? <Alert tone="danger">{error}</Alert> : null}
        <div className="flex justify-end gap-2">
          <Button type="button" onClick={onClose}>
            {t.common.cancel}
          </Button>
          <Button
            type="submit"
            variant="primary"
            disabled={mutation.isPending || !locationId || !reason.trim() || filled === 0}
          >
            {t.common.save}
          </Button>
        </div>
      </form>
    </Dialog>
  )
}

export type { StockCount }
