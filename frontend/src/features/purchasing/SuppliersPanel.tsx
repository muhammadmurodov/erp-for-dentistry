import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useI18n } from '@/i18n'
import { useAuth } from '@/features/auth/AuthProvider'
import { api } from '@/lib/api'
import {
  useGoodsReceipts,
  useSupplierBalance,
  useSuppliers,
  useUninvoicedLines,
} from '@/lib/queries'
import { describeError, errorRequestId } from '@/lib/errorMessage'
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
  Table,
  Td,
  Textarea,
  Th,
} from '@/components/ui'
import type { Supplier } from '@/lib/types'

/** Who the clinic buys from, and what it owes each of them. */
export function SuppliersPanel() {
  const { t } = useI18n()
  const { can } = useAuth()
  const mayManage = can('purchasing.supplier.manage')
  const [query, setQuery] = useState('')
  const [openId, setOpenId] = useState<string | null>(null)
  const [adding, setAdding] = useState(false)

  const suppliers = useSuppliers({ q: query || undefined }, true)

  if (openId) {
    const supplier = (suppliers.data ?? []).find((s) => s.id === openId)
    if (supplier) {
      return <SupplierDetail supplier={supplier} onBack={() => setOpenId(null)} />
    }
  }

  return (
    <>
      <Card>
        <CardHeader
          title={t.purchasing.suppliersTab}
          actions={
            <div className="flex flex-wrap items-center gap-2">
              <label className="sr-only" htmlFor="supplier-search">
                {t.purchasing.search}
              </label>
              <Input
                id="supplier-search"
                value={query}
                placeholder={t.purchasing.search}
                onChange={(event) => setQuery(event.target.value)}
                className="w-56"
              />
              {mayManage ? (
                <Button size="sm" variant="primary" onClick={() => setAdding(true)}>
                  {t.purchasing.addSupplier}
                </Button>
              ) : null}
            </div>
          }
        />
        {suppliers.isLoading ? (
          <LoadingState label={t.common.loading} />
        ) : suppliers.isError ? (
          <ErrorState
            title={t.errors.loadFailedTitle}
            message={describeError(suppliers.error, t)}
            requestId={errorRequestId(suppliers.error)}
            requestIdLabel={t.common.requestId}
            onRetry={() => void suppliers.refetch()}
            retryLabel={t.common.retry}
          />
        ) : (suppliers.data ?? []).length === 0 ? (
          <EmptyState title={t.purchasing.noSuppliers} />
        ) : (
          <Table>
            <thead>
              <tr>
                <Th>{t.purchasing.reference}</Th>
                <Th>{t.purchasing.supplierName}</Th>
                <Th>{t.purchasing.taxNumber}</Th>
                <Th>{t.purchasing.phone}</Th>
                <Th>{t.common.actions}</Th>
              </tr>
            </thead>
            <tbody>
              {(suppliers.data ?? []).map((supplier) => (
                <tr key={supplier.id}>
                  <Td>
                    <span className="font-mono text-xs">{supplier.code}</span>
                  </Td>
                  <Td>
                    {supplier.name}
                    {!supplier.is_active ? (
                      <span className="ml-2">
                        <Badge tone="neutral">{t.common.inactive}</Badge>
                      </span>
                    ) : null}
                  </Td>
                  <Td>{supplier.tax_number ?? '—'}</Td>
                  <Td>{supplier.phone ?? '—'}</Td>
                  <Td>
                    <div className="flex justify-end">
                      <Button size="sm" onClick={() => setOpenId(supplier.id)}>
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

      {adding ? (
        <SupplierDialog
          onClose={() => setAdding(false)}
          onCreated={(supplier) => {
            setAdding(false)
            setOpenId(supplier.id)
          }}
        />
      ) : null}
    </>
  )
}

function SupplierDetail({
  supplier,
  onBack,
}: {
  supplier: Supplier
  onBack: () => void
}) {
  const { t, formatDate, formatMoney } = useI18n()
  const balance = useSupplierBalance(supplier.id, true)
  const receipts = useGoodsReceipts({ supplier_id: supplier.id }, true)
  const uninvoiced = useUninvoicedLines(supplier.id, true)

  return (
    <div className="space-y-4">
      <Button size="sm" onClick={onBack}>
        ← {t.purchasing.back}
      </Button>

      <Card>
        <CardHeader
          title={supplier.name}
          description={`${t.purchasing.reference}: ${supplier.code}`}
        />
        <DescriptionList
          items={[
            { label: t.purchasing.taxNumber, value: supplier.tax_number ?? '—' },
            { label: t.purchasing.phone, value: supplier.phone ?? '—' },
            { label: t.purchasing.email, value: supplier.email ?? '—' },
            { label: t.purchasing.address, value: supplier.address ?? '—' },
            ...(supplier.notes
              ? [{ label: t.purchasing.notes, value: supplier.notes }]
              : []),
          ]}
        />
      </Card>

      {balance.data ? (
        <Card>
          <CardHeader title={t.purchasing.payablesTab} />
          <DescriptionList
            items={[
              {
                label: t.purchasing.billed,
                value: formatMoney(balance.data.billed, balance.data.currency),
              },
              {
                label: t.purchasing.paidSoFar,
                value: formatMoney(balance.data.paid, balance.data.currency),
              },
              {
                label: t.purchasing.outstanding,
                value:
                  Number(balance.data.outstanding) === 0 ? (
                    formatMoney(balance.data.outstanding, balance.data.currency)
                  ) : (
                    <span style={{ color: 'var(--warning)' }}>
                      {formatMoney(balance.data.outstanding, balance.data.currency)}
                    </span>
                  ),
              },
              {
                label: t.purchasing.advancesHeld,
                value: formatMoney(
                  balance.data.advances_available,
                  balance.data.currency,
                ),
              },
            ]}
          />
        </Card>
      ) : null}

      <Card>
        <CardHeader
          title={t.purchasing.uninvoicedHeading}
          description={t.purchasing.uninvoicedHint}
        />
        {(uninvoiced.data ?? []).length === 0 ? (
          <EmptyState title={t.purchasing.nothingUninvoiced} />
        ) : (
          <Table>
            <thead>
              <tr>
                <Th>{t.purchasing.receivedOn}</Th>
                <Th>{t.purchasing.reference}</Th>
                <Th>{t.purchasing.description}</Th>
                <Th numeric>{t.purchasing.quantity}</Th>
              </tr>
            </thead>
            <tbody>
              {(uninvoiced.data ?? []).map((line) => (
                <tr key={line.id}>
                  <Td>{formatDate(line.received_on)}</Td>
                  <Td>
                    <span className="font-mono text-xs">{line.receipt_code}</span>
                  </Td>
                  <Td>{line.description}</Td>
                  <Td numeric>
                    {line.quantity}
                    {line.unit ? ` ${line.unit}` : ''}
                  </Td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </Card>

      <Card>
        <CardHeader title={t.purchasing.receivingTab} />
        {(receipts.data ?? []).length === 0 ? (
          <EmptyState title={t.purchasing.noReceipts} />
        ) : (
          <Table>
            <thead>
              <tr>
                <Th>{t.purchasing.receivedOn}</Th>
                <Th>{t.purchasing.reference}</Th>
                <Th>{t.purchasing.deliveryNote}</Th>
              </tr>
            </thead>
            <tbody>
              {(receipts.data ?? []).map((receipt) => (
                <tr key={receipt.id}>
                  <Td>{formatDate(receipt.received_on)}</Td>
                  <Td>
                    <span className="font-mono text-xs">{receipt.code}</span>
                  </Td>
                  <Td>{receipt.delivery_note ?? '—'}</Td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </Card>
    </div>
  )
}

function SupplierDialog({
  onClose,
  onCreated,
}: {
  onClose: () => void
  onCreated: (supplier: Supplier) => void
}) {
  const { t } = useI18n()
  const queryClient = useQueryClient()
  const [name, setName] = useState('')
  const [taxNumber, setTaxNumber] = useState('')
  const [phone, setPhone] = useState('')
  const [email, setEmail] = useState('')
  const [address, setAddress] = useState('')
  const [notes, setNotes] = useState('')
  const [error, setError] = useState<string | null>(null)

  const create = useMutation({
    mutationFn: () =>
      api.post<Supplier>('/purchasing/suppliers', {
        name: name.trim(),
        tax_number: taxNumber.trim() || null,
        phone: phone.trim() || null,
        email: email.trim() || null,
        address: address.trim() || null,
        notes: notes.trim() || null,
      }),
    onSuccess: (supplier) => {
      void queryClient.invalidateQueries({ queryKey: ['purchasing'] })
      onCreated(supplier)
    },
    onError: (caught) => setError(describeError(caught, t)),
  })

  return (
    <Dialog
      open
      size="lg"
      onClose={onClose}
      title={t.purchasing.addSupplier}
      closeLabel={t.common.close}
      footer={
        <>
          <Button onClick={onClose} disabled={create.isPending}>
            {t.common.cancel}
          </Button>
          <Button
            variant="primary"
            disabled={name.trim() === '' || create.isPending}
            loading={create.isPending}
            onClick={() => create.mutate()}
          >
            {t.common.save}
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label={t.purchasing.supplierName} required htmlFor="supplier-name">
            <Input
              id="supplier-name"
              value={name}
              onChange={(event) => setName(event.target.value)}
            />
          </Field>
          <Field label={t.purchasing.taxNumber} htmlFor="supplier-tax">
            <Input
              id="supplier-tax"
              value={taxNumber}
              onChange={(event) => setTaxNumber(event.target.value)}
            />
          </Field>
          <Field label={t.purchasing.phone} htmlFor="supplier-phone">
            <Input
              id="supplier-phone"
              value={phone}
              onChange={(event) => setPhone(event.target.value)}
            />
          </Field>
          <Field label={t.purchasing.email} htmlFor="supplier-email">
            <Input
              id="supplier-email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
            />
          </Field>
        </div>
        <Field label={t.purchasing.address} htmlFor="supplier-address">
          <Input
            id="supplier-address"
            value={address}
            onChange={(event) => setAddress(event.target.value)}
          />
        </Field>
        <Field label={t.purchasing.notes} htmlFor="supplier-notes">
          <Textarea
            id="supplier-notes"
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
