import { useState } from 'react'
import { useI18n } from '@/i18n'
import { useReportingReceivables } from '@/lib/queries'
import { describeError, errorRequestId } from '@/lib/errorMessage'
import {
  Card,
  CardHeader,
  EmptyState,
  ErrorState,
  LoadingState,
  Table,
  Td,
  Th,
} from '@/components/ui'
import { todayInClinic } from '@/lib/clinicDate'
import { ExportButtons } from './shared'

/**
 * What patients owe, by how overdue it is.
 *
 * The same service the cashier's screen uses, not a second implementation:
 * a report that computed debt its own way would eventually disagree with
 * the figure somebody is asking a patient for.
 */
export function ReceivablesPanel() {
  const { t, formatMoney } = useI18n()
  const [asOf, setAsOf] = useState(todayInClinic())
  const report = useReportingReceivables(asOf, true)

  return (
    <Card>
      <CardHeader
        title={t.reports.receivablesTab}
        actions={
          <ExportButtons
            path="/reporting/receivables-ageing/export"
            params={{ as_of: asOf }}
          />
        }
      />
      <div className="px-4 pb-4 print:hidden">
        <label className="text-sm">
          <span className="mb-1 block text-[var(--text-muted)]">{t.reports.asOf}</span>
          <input
            type="date"
            aria-label={t.reports.asOf}
            value={asOf}
            onChange={(event) => setAsOf(event.target.value)}
            className="rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3 py-2"
          />
        </label>
      </div>

      {report.isLoading ? (
        <LoadingState label={t.common.loading} />
      ) : report.isError || !report.data ? (
        <ErrorState
          title={t.errors.loadFailedTitle}
          message={describeError(report.error, t)}
          requestId={errorRequestId(report.error)}
          requestIdLabel={t.common.requestId}
          onRetry={() => void report.refetch()}
          retryLabel={t.common.retry}
        />
      ) : report.data.customers.length === 0 ? (
        <EmptyState title={t.reports.nobodyOwesAnything} />
      ) : (
        <Table>
          <thead>
            <tr>
              <Th>{t.reports.patient}</Th>
              {report.data.bucket_totals.map((bucket) => (
                <Th key={bucket.label} numeric>
                  {bucket.label}
                </Th>
              ))}
              <Th numeric>{t.reports.total}</Th>
            </tr>
          </thead>
          <tbody>
            {report.data.customers.map((customer) => (
              <tr key={customer.customer_id}>
                <Td>{customer.customer_name}</Td>
                {report.data.bucket_totals.map((bucket) => {
                  const amount =
                    customer.buckets.find((b) => b.label === bucket.label)?.amount ?? '0.00'
                  return (
                    <Td key={bucket.label} numeric>
                      {formatMoney(amount)}
                    </Td>
                  )
                })}
                <Td numeric>
                  <strong>{formatMoney(customer.total_outstanding)}</strong>
                </Td>
              </tr>
            ))}
            <tr className="border-t-2 border-[var(--border)]">
              <Td>
                <strong>{t.reports.total}</strong>
              </Td>
              {report.data.bucket_totals.map((bucket) => (
                <Td key={bucket.label} numeric>
                  <strong>{formatMoney(bucket.amount)}</strong>
                </Td>
              ))}
              <Td numeric>
                <strong>{formatMoney(report.data.total_outstanding)}</strong>
              </Td>
            </tr>
          </tbody>
        </Table>
      )}
    </Card>
  )
}
