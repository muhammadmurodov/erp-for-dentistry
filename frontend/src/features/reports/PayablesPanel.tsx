import { useState } from 'react'
import { useI18n } from '@/i18n'
import { useReportingPayables } from '@/lib/queries'
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
 * What the clinic owes suppliers, by how overdue it is.
 *
 * An empty report says *why* it is empty. A clinic with no suppliers yet
 * should read that, not a blank table that looks like a failure.
 */
export function PayablesPanel() {
  const { t, formatMoney } = useI18n()
  const [asOf, setAsOf] = useState(todayInClinic())
  const report = useReportingPayables(asOf, true)

  return (
    <Card>
      <CardHeader
        title={t.reports.payablesTab}
        actions={
          <ExportButtons path="/reporting/payables-ageing/export" params={{ as_of: asOf }} />
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
      ) : report.data.no_suppliers_yet ? (
        <EmptyState title={t.reports.noSuppliersYet} body={t.reports.noSuppliersYetBody} />
      ) : report.data.rows.length === 0 ? (
        <EmptyState title={t.reports.nothingOwedToSuppliers} />
      ) : (
        <Table>
          <thead>
            <tr>
              <Th>{t.reports.supplier}</Th>
              <Th numeric>{t.reports.current}</Th>
              <Th numeric>1–30</Th>
              <Th numeric>31–60</Th>
              <Th numeric>61–90</Th>
              <Th numeric>90+</Th>
              <Th numeric>{t.reports.total}</Th>
            </tr>
          </thead>
          <tbody>
            {report.data.rows.map((row) => (
              <tr key={row.supplier_id}>
                <Td>{row.supplier_name}</Td>
                <Td numeric>{formatMoney(row.current)}</Td>
                <Td numeric>{formatMoney(row.days_1_30)}</Td>
                <Td numeric>{formatMoney(row.days_31_60)}</Td>
                <Td numeric>{formatMoney(row.days_61_90)}</Td>
                <Td numeric>{formatMoney(row.days_over_90)}</Td>
                <Td numeric>
                  <strong>{formatMoney(row.total)}</strong>
                </Td>
              </tr>
            ))}
            <tr className="border-t-2 border-[var(--border)]">
              <Td colSpan={6}>
                <strong>{t.reports.total}</strong>
              </Td>
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
