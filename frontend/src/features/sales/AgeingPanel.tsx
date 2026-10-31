import { useState } from 'react'
import { useI18n } from '@/i18n'
import { useAuth } from '@/features/auth/AuthProvider'
import { useReceivablesAgeing } from '@/lib/queries'
import { describeError, errorRequestId } from '@/lib/errorMessage'
import {
  Alert,
  Card,
  CardHeader,
  EmptyState,
  ErrorState,
  Input,
  LoadingState,
  Table,
  Td,
  Th,
} from '@/components/ui'

/** Blank reads better than a column of zeros. */
function amount(value: string): string {
  return value === '0' || value === '0.00' ? '' : value
}

export function AgeingPanel() {
  const { t, formatDate } = useI18n()
  const { can } = useAuth()
  const mayRead = can('reporting.financial.read')
  const [asOf, setAsOf] = useState(() => new Date().toISOString().slice(0, 10))
  const report = useReceivablesAgeing(mayRead, asOf)

  if (!mayRead) return <Alert tone="warning">{t.moduleStatus.noAccess}</Alert>

  const bucketLabel = (label: string) =>
    label === 'current' ? t.sales.current : label

  return (
    <Card>
      <CardHeader
        title={t.sales.ageingTab}
        description={`${t.sales.asOf} ${formatDate(asOf)}`}
        actions={
          <>
            <label htmlFor="ageing-date" className="sr-only">
              {t.sales.asOf}
            </label>
            <Input
              id="ageing-date"
              type="date"
              value={asOf}
              onChange={(event) => setAsOf(event.target.value)}
              className="w-36 py-1.5 text-xs"
            />
          </>
        }
      />

      {report.isLoading ? (
        <LoadingState label={t.common.loading} />
      ) : report.isError ? (
        <ErrorState
          title={t.errors.loadFailedTitle}
          message={describeError(report.error, t)}
          requestId={errorRequestId(report.error)}
          requestIdLabel={t.common.requestId}
          onRetry={() => void report.refetch()}
          retryLabel={t.common.retry}
        />
      ) : !report.data || report.data.customers.length === 0 ? (
        <EmptyState title={t.sales.emptyAgeing} />
      ) : (
        <Table>
          <thead>
            <tr>
              <Th>{t.sales.customer}</Th>
              {report.data.bucket_totals.map((bucket) => (
                <Th key={bucket.label} numeric>
                  {bucketLabel(bucket.label)}
                </Th>
              ))}
              <Th numeric>{t.sales.totalOutstanding}</Th>
            </tr>
          </thead>
          <tbody>
            {report.data.customers.map((row) => (
              <tr key={row.customer_id}>
                <Td>
                  {row.customer_name}
                  <span className="block font-mono text-[10px] text-[var(--text-muted)]">
                    {row.customer_code}
                  </span>
                </Td>
                {row.buckets.map((bucket) => (
                  <Td key={bucket.label} numeric>
                    {amount(bucket.amount)}
                  </Td>
                ))}
                <Td numeric className="font-medium">
                  {row.total_outstanding}
                </Td>
              </tr>
            ))}
            <tr>
              <Td className="font-semibold">{t.accounting.total}</Td>
              {report.data.bucket_totals.map((bucket) => (
                <Td key={bucket.label} numeric className="font-semibold">
                  {amount(bucket.amount)}
                </Td>
              ))}
              <Td numeric className="font-semibold">
                {report.data.total_outstanding}
              </Td>
            </tr>
          </tbody>
        </Table>
      )}
    </Card>
  )
}
