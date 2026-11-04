import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useI18n } from '@/i18n'
import { useAuth } from '@/features/auth/AuthProvider'
import { api } from '@/lib/api'
import { useInventoryCategories, useInventoryItems, useStockLocations } from '@/lib/queries'
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
  Textarea,
  Th,
} from '@/components/ui'
import { displayQuantity } from '@/lib/inventory'
import type { InventoryItem } from '@/lib/types'

/**
 * The catalogue: materials, what they are grouped into, where they are kept.
 *
 * Maintained once and then hardly opened, which is why it sits behind the
 * stock tab rather than in front of it.
 */
export function MaterialsPanel() {
  const { t } = useI18n()
  const { can } = useAuth()
  const mayManage = can('inventory.item.manage')

  const [search, setSearch] = useState('')
  const [adding, setAdding] = useState(false)
  const [editing, setEditing] = useState<InventoryItem | null>(null)
  const [addingLocation, setAddingLocation] = useState(false)
  const [addingCategory, setAddingCategory] = useState(false)

  const items = useInventoryItems({ search: search || undefined }, true)
  const categories = useInventoryCategories(true)
  const locations = useStockLocations(true)

  const categoryName = (id: string | null) =>
    id ? ((categories.data ?? []).find((row) => row.id === id)?.name ?? '—') : '—'

  return (
    <div className="space-y-5">
      <Card>
        <CardHeader
          title={t.inventory.materialsTab}
          actions={
            <div className="flex flex-wrap items-center gap-2">
              <label className="sr-only" htmlFor="material-search">
                {t.inventory.search}
              </label>
              <Input
                id="material-search"
                value={search}
                placeholder={t.inventory.search}
                onChange={(event) => setSearch(event.target.value)}
                className="w-56"
              />
              {mayManage ? (
                <Button size="sm" variant="primary" onClick={() => setAdding(true)}>
                  {t.inventory.addMaterial}
                </Button>
              ) : null}
            </div>
          }
        />
        {items.isLoading ? (
          <LoadingState label={t.common.loading} />
        ) : items.isError ? (
          <ErrorState
            title={t.errors.loadFailedTitle}
            message={describeError(items.error, t)}
            requestId={errorRequestId(items.error)}
            requestIdLabel={t.common.requestId}
            onRetry={() => void items.refetch()}
            retryLabel={t.common.retry}
          />
        ) : (items.data ?? []).length === 0 ? (
          <EmptyState title={t.inventory.noMaterials} />
        ) : (
          <Table>
            <thead>
              <tr>
                <Th>{t.inventory.reference}</Th>
                <Th>{t.inventory.material}</Th>
                <Th>{t.inventory.category}</Th>
                <Th>{t.inventory.unit}</Th>
                <Th numeric>{t.inventory.reorderLevel}</Th>
                <Th>{t.common.actions}</Th>
              </tr>
            </thead>
            <tbody>
              {(items.data ?? []).map((item) => (
                <tr key={item.id}>
                  <Td>
                    <span className="font-mono text-xs">{item.code}</span>
                  </Td>
                  <Td>
                    {item.name}
                    {item.is_batch_tracked ? (
                      <span className="ml-2">
                        <Badge tone="brand">{t.inventory.batchTracked}</Badge>
                      </span>
                    ) : null}
                    {!item.is_active ? (
                      <span className="ml-2">
                        <Badge tone="neutral">{t.common.inactive}</Badge>
                      </span>
                    ) : null}
                  </Td>
                  <Td>{categoryName(item.category_id)}</Td>
                  <Td>{item.unit}</Td>
                  <Td numeric>{item.reorder_level ? displayQuantity(item.reorder_level) : '—'}</Td>
                  <Td>
                    <div className="flex justify-end">
                      {mayManage ? (
                        <Button size="sm" onClick={() => setEditing(item)}>
                          {t.inventory.edit}
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

      <Card>
        <CardHeader
          title={t.inventory.locations}
          actions={
            mayManage ? (
              <Button size="sm" onClick={() => setAddingLocation(true)}>
                {t.inventory.addLocation}
              </Button>
            ) : null
          }
        />
        {(locations.data ?? []).length === 0 ? (
          <EmptyState title={t.inventory.noLocations} body={t.inventory.noLocationsBody} />
        ) : (
          <Table>
            <thead>
              <tr>
                <Th>{t.inventory.reference}</Th>
                <Th>{t.inventory.location}</Th>
                <Th>{t.inventory.isDefault}</Th>
              </tr>
            </thead>
            <tbody>
              {(locations.data ?? []).map((location) => (
                <tr key={location.id}>
                  <Td>
                    <span className="font-mono text-xs">{location.code}</span>
                  </Td>
                  <Td>{location.name}</Td>
                  <Td>
                    {location.is_default ? <Badge tone="success">{t.inventory.yes}</Badge> : '—'}
                  </Td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </Card>

      <Card>
        <CardHeader
          title={t.inventory.categories}
          actions={
            mayManage ? (
              <Button size="sm" onClick={() => setAddingCategory(true)}>
                {t.inventory.addCategory}
              </Button>
            ) : null
          }
        />
        {(categories.data ?? []).length === 0 ? (
          <EmptyState title={t.inventory.noCategories} />
        ) : (
          <Table>
            <thead>
              <tr>
                <Th>{t.inventory.reference}</Th>
                <Th>{t.inventory.category}</Th>
              </tr>
            </thead>
            <tbody>
              {(categories.data ?? []).map((category) => (
                <tr key={category.id}>
                  <Td>
                    <span className="font-mono text-xs">{category.code}</span>
                  </Td>
                  <Td>{category.name}</Td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </Card>

      {adding ? <MaterialDialog onClose={() => setAdding(false)} /> : null}
      {editing ? (
        <MaterialDialog item={editing} onClose={() => setEditing(null)} />
      ) : null}
      {addingLocation ? <LocationDialog onClose={() => setAddingLocation(false)} /> : null}
      {addingCategory ? <CategoryDialog onClose={() => setAddingCategory(false)} /> : null}
    </div>
  )
}

function MaterialDialog({
  item,
  onClose,
}: {
  item?: InventoryItem
  onClose: () => void
}) {
  const { t } = useI18n()
  const queryClient = useQueryClient()
  const categories = useInventoryCategories(true)
  const [code, setCode] = useState(item?.code ?? '')
  const [name, setName] = useState(item?.name ?? '')
  const [unit, setUnit] = useState(item?.unit ?? '')
  const [categoryId, setCategoryId] = useState(item?.category_id ?? '')
  const [reorderLevel, setReorderLevel] = useState(item?.reorder_level ?? '')
  const [batchTracked, setBatchTracked] = useState(item?.is_batch_tracked ?? false)
  const [notes, setNotes] = useState(item?.notes ?? '')
  const [error, setError] = useState<string | null>(null)

  const mutation = useMutation({
    mutationFn: () =>
      item
        ? api.patch(`/inventory/items/${item.id}`, {
            name,
            unit,
            category_id: categoryId || null,
            reorder_level: reorderLevel || null,
            is_batch_tracked: batchTracked,
            notes: notes || null,
          })
        : api.post('/inventory/items', {
            code,
            name,
            unit,
            category_id: categoryId || null,
            reorder_level: reorderLevel || null,
            is_batch_tracked: batchTracked,
            notes: notes || null,
          }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['inventory'] })
      onClose()
    },
    onError: (err) => setError(describeError(err, t)),
  })

  return (
    <Dialog open closeLabel={t.common.close} title={item ? t.inventory.editMaterial : t.inventory.addMaterial} onClose={onClose}>
      <form
        className="space-y-3"
        onSubmit={(event) => {
          event.preventDefault()
          setError(null)
          mutation.mutate()
        }}
      >
        {item ? null : (
          <Field label={t.inventory.reference} htmlFor="material-code">
            <Input
              id="material-code"
              value={code}
              onChange={(event) => setCode(event.target.value)}
              required
            />
          </Field>
        )}
        <Field label={t.inventory.material} htmlFor="material-name">
          <Input
            id="material-name"
            value={name}
            onChange={(event) => setName(event.target.value)}
            required
          />
        </Field>
        <Field label={t.inventory.unit} htmlFor="material-unit" hint={t.inventory.unitHint}>
          <Input
            id="material-unit"
            value={unit}
            onChange={(event) => setUnit(event.target.value)}
            required
          />
        </Field>
        <Field label={t.inventory.category} htmlFor="material-category">
          <Select
            id="material-category"
            value={categoryId}
            onChange={(event) => setCategoryId(event.target.value)}
          >
            <option value="">—</option>
            {(categories.data ?? []).map((category) => (
              <option key={category.id} value={category.id}>
                {category.name}
              </option>
            ))}
          </Select>
        </Field>
        <Field
          label={t.inventory.reorderLevel}
          htmlFor="material-reorder"
          hint={t.inventory.reorderHint}
        >
          <Input
            id="material-reorder"
            value={reorderLevel ?? ''}
            inputMode="decimal"
            onChange={(event) => setReorderLevel(event.target.value)}
          />
        </Field>
        <Checkbox
          label={t.inventory.batchTracked}
          checked={batchTracked}
          onChange={(event) => setBatchTracked(event.target.checked)}
        />
        {item && !item.is_batch_tracked && batchTracked ? (
          <Alert tone="info">{t.inventory.batchSwitchWarning}</Alert>
        ) : null}
        <Field label={t.inventory.notes} htmlFor="material-notes">
          <Textarea
            id="material-notes"
            value={notes ?? ''}
            onChange={(event) => setNotes(event.target.value)}
          />
        </Field>
        {error ? <Alert tone="danger">{error}</Alert> : null}
        <div className="flex justify-end gap-2">
          <Button type="button" onClick={onClose}>
            {t.common.cancel}
          </Button>
          <Button type="submit" variant="primary" disabled={mutation.isPending}>
            {t.common.save}
          </Button>
        </div>
      </form>
    </Dialog>
  )
}

function LocationDialog({ onClose }: { onClose: () => void }) {
  const { t } = useI18n()
  const queryClient = useQueryClient()
  const [code, setCode] = useState('')
  const [name, setName] = useState('')
  const [isDefault, setIsDefault] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const mutation = useMutation({
    mutationFn: () =>
      api.post('/inventory/locations', { code, name, is_default: isDefault }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['inventory'] })
      onClose()
    },
    onError: (err) => setError(describeError(err, t)),
  })

  return (
    <Dialog open closeLabel={t.common.close} title={t.inventory.addLocation} onClose={onClose}>
      <form
        className="space-y-3"
        onSubmit={(event) => {
          event.preventDefault()
          setError(null)
          mutation.mutate()
        }}
      >
        <Field label={t.inventory.reference} htmlFor="location-code">
          <Input
            id="location-code"
            value={code}
            onChange={(event) => setCode(event.target.value)}
            required
          />
        </Field>
        <Field label={t.inventory.location} htmlFor="location-name">
          <Input
            id="location-name"
            value={name}
            onChange={(event) => setName(event.target.value)}
            required
          />
        </Field>
        <Checkbox
          label={t.inventory.isDefault}
          checked={isDefault}
          onChange={(event) => setIsDefault(event.target.checked)}
        />
        {error ? <Alert tone="danger">{error}</Alert> : null}
        <div className="flex justify-end gap-2">
          <Button type="button" onClick={onClose}>
            {t.common.cancel}
          </Button>
          <Button type="submit" variant="primary" disabled={mutation.isPending}>
            {t.common.save}
          </Button>
        </div>
      </form>
    </Dialog>
  )
}

function CategoryDialog({ onClose }: { onClose: () => void }) {
  const { t } = useI18n()
  const queryClient = useQueryClient()
  const [code, setCode] = useState('')
  const [name, setName] = useState('')
  const [error, setError] = useState<string | null>(null)

  const mutation = useMutation({
    mutationFn: () => api.post('/inventory/categories', { code, name }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['inventory'] })
      onClose()
    },
    onError: (err) => setError(describeError(err, t)),
  })

  return (
    <Dialog open closeLabel={t.common.close} title={t.inventory.addCategory} onClose={onClose}>
      <form
        className="space-y-3"
        onSubmit={(event) => {
          event.preventDefault()
          setError(null)
          mutation.mutate()
        }}
      >
        <Field label={t.inventory.reference} htmlFor="category-code">
          <Input
            id="category-code"
            value={code}
            onChange={(event) => setCode(event.target.value)}
            required
          />
        </Field>
        <Field label={t.inventory.category} htmlFor="category-name">
          <Input
            id="category-name"
            value={name}
            onChange={(event) => setName(event.target.value)}
            required
          />
        </Field>
        {error ? <Alert tone="danger">{error}</Alert> : null}
        <div className="flex justify-end gap-2">
          <Button type="button" onClick={onClose}>
            {t.common.cancel}
          </Button>
          <Button type="submit" variant="primary" disabled={mutation.isPending}>
            {t.common.save}
          </Button>
        </div>
      </form>
    </Dialog>
  )
}
