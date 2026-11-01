import { useState } from 'react'
import { useI18n } from '@/i18n'
import { usePatient } from '@/lib/queries'
import { describeError, errorRequestId } from '@/lib/errorMessage'
import {
  Badge,
  Button,
  Card,
  CardHeader,
  DescriptionList,
  EmptyState,
  ErrorState,
  LoadingState,
  Table,
  Td,
  Th,
} from '@/components/ui'
import type { SettlementStatus } from '@/lib/types'
import { PaymentPanel } from './PaymentPanel'

/**
 * One patient's account: who they are, what they owe, and their bills.
 *
 * Every figure here is read from the sales service, which derives it from
 * invoices and live allocations. The clinic module stores no balance, so
 * there is no figure on this page that could disagree with the ledger behind
 * it.
 *
 * Deliberately free of clinical content. This is the page a cashier opens to
 * take money, and a diagnosis is none of their business — it lives behind
 * `clinic.clinical.read`, which they do not hold.
 */
export function PatientAccount({
  patientId,
  onBack,
}: {
  patientId: string
  onBack: () => void
}) {
  const { t, formatDate, formatMoney } = useI18n()
  const detail = usePatient(patientId)
  const [payingInvoiceId, setPayingInvoiceId] = useState<string | null>(null)

  if (detail.isLoading) return <LoadingState label={t.common.loading} />
  if (detail.isError || !detail.data) {
    return (
      <ErrorState
        title={t.errors.loadFailedTitle}
        message={describeError(detail.error, t)}
        requestId={errorRequestId(detail.error)}
        requestIdLabel={t.common.requestId}
        onRetry={() => void detail.refetch()}
        retryLabel={t.common.retry}
      />
    )
  }

  const { patient, balance, invoices } = detail.data
  const statusLabel: Record<SettlementStatus, string> = {
    UNPAID: t.clinic.statusUnpaid,
    PARTIALLY_PAID: t.clinic.statusPartial,
    FULLY_PAID: t.clinic.statusFull,
    WRITTEN_OFF: t.clinic.statusWrittenOff,
    NOT_PAYABLE: t.clinic.statusNotPayable,
  }
  const statusTone: Record<SettlementStatus, 'neutral' | 'success' | 'warning' | 'danger'> =
    {
      UNPAID: 'warning',
      PARTIALLY_PAID: 'warning',
      FULLY_PAID: 'success',
      WRITTEN_OFF: 'danger',
      NOT_PAYABLE: 'neutral',
    }

  return (
    <div className="flex flex-col gap-4">
      <Card>
        <CardHeader
          title={patient.display_name}
          description={`${patient.code} · ${t.clinic.registeredOn} ${formatDate(patient.registered_on)}`}
          actions={
            <Button size="sm" variant="ghost" onClick={onBack}>
              {t.common.back}
            </Button>
          }
        />
        <DescriptionList
          items={[
            { label: t.clinic.birthYear, value: patient.birth_year ?? '—' },
            { label: t.clinic.phone, value: patient.phone ?? '—' },
            { label: t.clinic.address, value: patient.address ?? '—' },
            {
              label: t.clinic.billableItems,
              value: detail.data.billable_items,
            },
            ...(detail.data.open_duplicate_reviews > 0
              ? [
                  {
                    label: t.clinic.openReviews,
                    value: (
                      <Badge tone="warning">{detail.data.open_duplicate_reviews}</Badge>
                    ),
                  },
                ]
              : []),
          ]}
        />
      </Card>

      <Card>
        <CardHeader title={t.clinic.balanceHeading} />
        <DescriptionList
          items={[
            { label: t.clinic.invoiced, value: formatMoney(balance.invoiced) },
            { label: t.clinic.paid, value: formatMoney(balance.paid) },
            {
              label: t.clinic.outstanding,
              value:
                balance.outstanding === '0.00' ? (
                  formatMoney(balance.outstanding)
                ) : (
                  // The qarzdorlik, marked rather than merely shown.
                  <span style={{ color: 'var(--warning)' }}>
                    {formatMoney(balance.outstanding)}
                  </span>
                ),
            },
            {
              label: t.clinic.advancesAvailable,
              value: formatMoney(balance.advances_available),
            },
            ...(balance.written_off === '0.00'
              ? []
              : [
                  {
                    label: t.clinic.writtenOff,
                    value: formatMoney(balance.written_off),
                  },
                ]),
          ]}
        />
      </Card>

      <Card>
        <CardHeader title={t.clinic.invoicesHeading} />
        {invoices.length === 0 ? (
          <EmptyState title={t.clinic.emptyInvoices} />
        ) : (
          <Table>
            <thead>
              <tr>
                <Th>{t.clinic.invoiceNumber}</Th>
                <Th>{t.clinic.issueDate}</Th>
                <Th numeric>{t.clinic.total}</Th>
                <Th numeric>{t.clinic.paid}</Th>
                <Th numeric>{t.clinic.remaining}</Th>
                <Th>{t.sales.status}</Th>
                <Th>{t.common.actions}</Th>
              </tr>
            </thead>
            <tbody>
              {invoices.map((invoice) => (
                <tr key={invoice.invoice_id}>
                  <Td>
                    <span className="font-mono text-xs">{invoice.number}</span>
                  </Td>
                  <Td>{formatDate(invoice.issue_date)}</Td>
                  <Td numeric>{formatMoney(invoice.total, invoice.currency)}</Td>
                  <Td numeric>{formatMoney(invoice.paid, invoice.currency)}</Td>
                  <Td numeric>{formatMoney(invoice.remaining, invoice.currency)}</Td>
                  <Td>
                    <Badge tone={statusTone[invoice.settlement_status]}>
                      {statusLabel[invoice.settlement_status]}
                    </Badge>
                  </Td>
                  <Td>
                    <Button
                      size="sm"
                      variant={
                        invoice.settlement_status === 'FULLY_PAID' ? 'ghost' : 'secondary'
                      }
                      onClick={() => setPayingInvoiceId(invoice.invoice_id)}
                    >
                      {t.clinic.takePayment}
                    </Button>
                  </Td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </Card>

      {payingInvoiceId ? (
        <PaymentPanel
          invoiceId={payingInvoiceId}
          onSettled={() => void detail.refetch()}
        />
      ) : null}
    </div>
  )
}
