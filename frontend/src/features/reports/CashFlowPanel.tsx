import { useI18n } from '@/i18n'
import { useCashFlow } from '@/lib/queries'
import { describeError, errorRequestId } from '@/lib/errorMessage'
import {
  Alert,
  Card,
  CardHeader,
  EmptyState,
  ErrorState,
  LoadingState,
  Table,
  Td,
  Th,
} from '@/components/ui'
import { ExportButtons, PeriodPicker, useReportPeriod } from './shared'

/**
 * What came in, what went out, and what it was for.
 *
 * Deliberately *not* split into operating, investing and financing: which
 * method the filing requires is an open question (A3), and a three-way split
 * nobody has approved would look like an answer. The screen says so.
 */
export function CashFlowPanel() {
  const { t, formatMoney } = useI18n()
  const period = useReportPeriod('year_to_date')
  const report = useCashFlow(
    { from_date: period.window.from, to_date: period.window.to },
    true,
  )

  return (
    <Card>
      <CardHeader
        title={t.reports.cashFlowTab}
        actions={
          <ExportButtons
            path="/reporting/cash-flow/export"
            params={{ from_date: period.window.from, to_date: period.window.to }}
          />
        }
      />
      <div className="px-4 pb-4 print:hidden">
        <PeriodPicker period={period} />
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
      ) : report.data.no_cash_accounts_configured ? (
        <EmptyState title={t.reports.noCashAccounts} body={t.reports.noCashAccountsBody} />
      ) : (
        <>
          <p className="px-4 text-sm text-[var(--text-muted)]">
            {report.data.from_date} — {report.data.to_date}
          </p>
          <div className="space-y-2 px-4 pb-4 text-sm">
            <Alert tone="info">{t.reports.notClassifiedYet}</Alert>
            {!report.data.reconciles ? (
              <Alert tone="danger">
                {t.reports.doesNotReconcile.replace(
                  '{amount}',
                  formatMoney(report.data.difference),
                )}
              </Alert>
            ) : null}
          </div>

          <Table>
            <thead>
              <tr>
                <Th>{t.reports.cashAccount}</Th>
                <Th numeric>{t.reports.opening}</Th>
                <Th numeric>{t.reports.movement}</Th>
                <Th numeric>{t.reports.closing}</Th>
              </tr>
            </thead>
            <tbody>
              {report.data.cash_accounts.map((row) => (
                <tr key={row.account_id}>
                  <Td>
                    <span className="font-mono text-xs">{row.account_code}</span>{' '}
                    {row.account_name}
                  </Td>
                  <Td numeric>{formatMoney(row.opening)}</Td>
                  <Td numeric>{formatMoney(row.movement)}</Td>
                  <Td numeric>{formatMoney(row.closing)}</Td>
                </tr>
              ))}
              <tr className="border-t-2 border-[var(--border)]">
                <Td>
                  <strong>{t.reports.cashInHandAndAtBank}</strong>
                </Td>
                <Td numeric>
                  <strong>{formatMoney(report.data.opening_balance)}</strong>
                </Td>
                <Td numeric>
                  <strong>{formatMoney(report.data.net_movement)}</strong>
                </Td>
                <Td numeric>
                  <strong>{formatMoney(report.data.closing_balance)}</strong>
                </Td>
              </tr>
            </tbody>
          </Table>

          <div className="grid gap-4 p-4 md:grid-cols-2">
            <Movements
              title={t.reports.received}
              lines={report.data.inflows}
              total={report.data.total_in}
              emptyLabel={t.reports.nothingReceived}
            />
            <Movements
              title={t.reports.paid}
              lines={report.data.outflows}
              total={report.data.total_out}
              emptyLabel={t.reports.nothingPaid}
            />
          </div>
        </>
      )}
    </Card>
  )
}

function Movements({
  title,
  lines,
  total,
  emptyLabel,
}: {
  title: string
  lines: { account_id: string; account_code: string; account_name: string; amount: string }[]
  total: string
  emptyLabel: string
}) {
  const { t, formatMoney } = useI18n()
  return (
    <div>
      <h3 className="mb-2 text-sm font-semibold">{title}</h3>
      {lines.length === 0 ? (
        <p className="text-sm text-[var(--text-muted)]">{emptyLabel}</p>
      ) : (
        <Table>
          <thead>
            <tr>
              <Th>{t.reports.account}</Th>
              <Th numeric>{t.reports.amount}</Th>
            </tr>
          </thead>
          <tbody>
            {lines.map((line) => (
              <tr key={line.account_id}>
                <Td>
                  <span className="font-mono text-xs">{line.account_code}</span>{' '}
                  {line.account_name}
                </Td>
                <Td numeric>{formatMoney(line.amount)}</Td>
              </tr>
            ))}
            <tr className="border-t-2 border-[var(--border)]">
              <Td>
                <strong>{t.reports.total}</strong>
              </Td>
              <Td numeric>
                <strong>{formatMoney(total)}</strong>
              </Td>
            </tr>
          </tbody>
        </Table>
      )}
    </div>
  )
}
