import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useI18n } from '@/i18n'
import { useAuth } from '@/features/auth/AuthProvider'
import { api } from '@/lib/api'
import { useCustomers, useServices } from '@/lib/queries'
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
  Table,
  Td,
  Th,
} from '@/components/ui'
import type { Customer, ServiceItem } from '@/lib/types'

export function ServicesPanel() {
  const { t, formatDate } = useI18n()
  const { can } = useAuth()
  const queryClient = useQueryClient()
  const [search, setSearch] = useState('')
  const [createOpen, setCreateOpen] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const mayRead = can('sales.service.read')
  const mayManage = can('sales.service.manage')
  const services = useServices(mayRead, { search: search.trim() || undefined })

  if (!mayRead) return <Alert tone="warning">{t.moduleStatus.noAccess}</Alert>
  if (services.isLoading) return <LoadingState label={t.common.loading} />
  if (services.isError) {
    return (
      <ErrorState
        title={t.errors.loadFailedTitle}
        message={describeError(services.error, t)}
        requestId={errorRequestId(services.error)}
        requestIdLabel={t.common.requestId}
        onRetry={() => void services.refetch()}
        retryLabel={t.common.retry}
      />
    )
  }

  const rows = services.data ?? []

  return (
    <>
      <Card>
        <CardHeader
          title={t.sales.servicesTab}
          actions={
            <>
              <label htmlFor="service-search" className="sr-only">
                {t.common.search}
              </label>
              <Input
                id="service-search"
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                placeholder={t.common.search}
                className="w-40 py-1.5 text-xs"
              />
              {mayManage ? (
                <Button
                  variant="primary"
                  size="sm"
                  onClick={() => setCreateOpen(true)}
                >
                  {t.sales.newService}
                </Button>
              ) : null}
            </>
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
          <EmptyState title={t.sales.emptyServices} />
        ) : (
          <Table>
            <thead>
              <tr>
                <Th>{t.sales.serviceCode}</Th>
                <Th>{t.sales.serviceName}</Th>
                <Th>{t.sales.mxikCode}</Th>
                <Th numeric>{t.sales.price}</Th>
                <Th>{t.sales.effectiveFrom}</Th>
              </tr>
            </thead>
            <tbody>
              {rows.map((service: ServiceItem) => (
                <tr key={service.id}>
                  <Td>
                    <span className="font-mono text-xs">{service.code}</span>
                  </Td>
                  <Td>{service.name}</Td>
                  <Td>
                    {service.mxik_code ? (
                      <span className="font-mono text-xs">{service.mxik_code}</span>
                    ) : (
                      <span className="text-xs text-[var(--text-muted)]">—</span>
                    )}
                  </Td>
                  <Td numeric>
                    {service.current_price ?? (
                      <span className="text-xs text-[var(--text-muted)]">
                        {t.common.noResults}
                      </span>
                    )}
                  </Td>
                  <Td>
                    {service.price_effective_from
                      ? formatDate(service.price_effective_from)
                      : '—'}
                  </Td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </Card>

      <CreateServiceDialog
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onCreated={() => {
          setCreateOpen(false)
          setError(null)
          setNotice(t.sales.serviceCreated)
          void queryClient.invalidateQueries({ queryKey: ['sales'] })
        }}
        onError={(message) => {
          setCreateOpen(false)
          setError(message)
        }}
      />
    </>
  )
}

function CreateServiceDialog({
  open,
  onClose,
  onCreated,
  onError,
}: {
  open: boolean
  onClose: () => void
  onCreated: () => void
  onError: (message: string) => void
}) {
  const { t } = useI18n()
  const [code, setCode] = useState('')
  const [name, setName] = useState('')
  const [mxik, setMxik] = useState('')
  const [price, setPrice] = useState('')

  const create = useMutation({
    mutationFn: () =>
      api.post<ServiceItem>('/sales/services', {
        code,
        name,
        mxik_code: mxik || null,
        // A string, never a JSON number: the API refuses floats for money.
        price: price || null,
        effective_from: new Date().toISOString().slice(0, 10),
      }),
    onSuccess: () => {
      setCode('')
      setName('')
      setMxik('')
      setPrice('')
      onCreated()
    },
    onError: (err) => onError(describeError(err, t)),
  })

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={t.sales.newService}
      closeLabel={t.common.close}
      footer={
        <>
          <Button onClick={onClose} disabled={create.isPending}>
            {t.common.cancel}
          </Button>
          <Button
            variant="primary"
            loading={create.isPending}
            disabled={!code || !name}
            onClick={() => create.mutate()}
          >
            {t.common.create}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label={t.sales.serviceCode} htmlFor="svc-code" required>
            <Input
              id="svc-code"
              value={code}
              onChange={(event) => setCode(event.target.value)}
            />
          </Field>
          <Field label={t.sales.price} htmlFor="svc-price">
            <Input
              id="svc-price"
              inputMode="decimal"
              className="tabular text-right"
              value={price}
              onChange={(event) => setPrice(event.target.value)}
            />
          </Field>
        </div>
        <Field label={t.sales.serviceName} htmlFor="svc-name" required>
          <Input
            id="svc-name"
            value={name}
            onChange={(event) => setName(event.target.value)}
          />
        </Field>
        <Field
          label={t.sales.mxikCode}
          htmlFor="svc-mxik"
          hint="ИКПУ / MXIK"
        >
          <Input
            id="svc-mxik"
            value={mxik}
            onChange={(event) => setMxik(event.target.value)}
          />
        </Field>
      </div>
    </Dialog>
  )
}

export function CustomersPanel() {
  const { t } = useI18n()
  const { can } = useAuth()
  const queryClient = useQueryClient()
  const [search, setSearch] = useState('')
  const [createOpen, setCreateOpen] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const mayRead = can('sales.invoice.read')
  const mayManage = can('sales.invoice.manage')
  const customers = useCustomers(mayRead, { search: search.trim() || undefined })

  if (!mayRead) return <Alert tone="warning">{t.moduleStatus.noAccess}</Alert>
  if (customers.isLoading) return <LoadingState label={t.common.loading} />

  const rows = customers.data ?? []

  return (
    <>
      <Card>
        <CardHeader
          title={t.sales.customersTab}
          actions={
            <>
              <label htmlFor="customer-search" className="sr-only">
                {t.common.search}
              </label>
              <Input
                id="customer-search"
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                placeholder={t.common.search}
                className="w-40 py-1.5 text-xs"
              />
              {mayManage ? (
                <Button
                  variant="primary"
                  size="sm"
                  onClick={() => setCreateOpen(true)}
                >
                  {t.sales.newCustomer}
                </Button>
              ) : null}
            </>
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
          <EmptyState title={t.sales.emptyCustomers} />
        ) : (
          <Table>
            <thead>
              <tr>
                <Th>{t.sales.customerCode}</Th>
                <Th>{t.sales.customerName}</Th>
                <Th>{t.organization.phone}</Th>
                <Th>{t.sales.status}</Th>
              </tr>
            </thead>
            <tbody>
              {rows.map((customer: Customer) => (
                <tr key={customer.id}>
                  <Td>
                    <span className="font-mono text-xs">{customer.code}</span>
                  </Td>
                  <Td>{customer.display_name}</Td>
                  <Td>{customer.phone ?? '—'}</Td>
                  <Td>
                    {customer.is_active ? (
                      <Badge tone="success">{t.common.active}</Badge>
                    ) : (
                      <Badge>{t.common.inactive}</Badge>
                    )}
                  </Td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </Card>

      <CreateCustomerDialog
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onCreated={() => {
          setCreateOpen(false)
          setError(null)
          setNotice(t.sales.customerCreated)
          void queryClient.invalidateQueries({ queryKey: ['sales'] })
        }}
        onError={(message) => {
          setCreateOpen(false)
          setError(message)
        }}
      />
    </>
  )
}

function CreateCustomerDialog({
  open,
  onClose,
  onCreated,
  onError,
}: {
  open: boolean
  onClose: () => void
  onCreated: () => void
  onError: (message: string) => void
}) {
  const { t } = useI18n()
  const [code, setCode] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [phone, setPhone] = useState('')

  const create = useMutation({
    mutationFn: () =>
      api.post<Customer>('/sales/customers', {
        code,
        display_name: displayName,
        phone: phone || null,
      }),
    onSuccess: () => {
      setCode('')
      setDisplayName('')
      setPhone('')
      onCreated()
    },
    onError: (err) => onError(describeError(err, t)),
  })

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={t.sales.newCustomer}
      closeLabel={t.common.close}
      footer={
        <>
          <Button onClick={onClose} disabled={create.isPending}>
            {t.common.cancel}
          </Button>
          <Button
            variant="primary"
            loading={create.isPending}
            disabled={!code || !displayName}
            onClick={() => create.mutate()}
          >
            {t.common.create}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <Field label={t.sales.customerCode} htmlFor="cust-code" required>
          <Input
            id="cust-code"
            value={code}
            onChange={(event) => setCode(event.target.value)}
          />
        </Field>
        <Field label={t.sales.customerName} htmlFor="cust-name" required>
          <Input
            id="cust-name"
            value={displayName}
            onChange={(event) => setDisplayName(event.target.value)}
          />
        </Field>
        <Field label={t.organization.phone} htmlFor="cust-phone">
          <Input
            id="cust-phone"
            type="tel"
            value={phone}
            onChange={(event) => setPhone(event.target.value)}
          />
        </Field>
      </div>
    </Dialog>
  )
}
