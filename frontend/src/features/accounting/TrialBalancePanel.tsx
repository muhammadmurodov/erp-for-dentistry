import { useState } from 'react'
import { useI18n } from '@/i18n'
import { useAuth } from '@/features/auth/AuthProvider'
import { useAccountStatement, useTrialBalance } from '@/lib/queries'
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
  Input,
  LoadingState,
  Table,
  Td,
  Th,
} from '@/components/ui'

/** Blank cells read better than a column of zeros in a trial balance. */
function amount(value: string): string {
  return value === '0' || value === '0.00' ? '' : value
}

export function TrialBalancePanel() {
  const { t, formatDate } = useI18n()
  const { can } = useAuth()
  const mayRead = can('reporting.financial.read')

  const thisYear = new Date().getFullYear()
  const [from, setFrom] = useState(`${thisYear}-01-01`)
  const [to, setTo] = useState(`${thisYear}-12-31`)
  const [statementFor, setStatementFor] = useState<string | null>(null)

  const report = useTrialBalance(mayRead, from, to)

  if (!mayRead) return <Alert tone="warning">{t.moduleStatus.noAccess}</Alert>

  return (
    <>
      <Card>
        <CardHeader
          title={t.accounting.trialBalanceTab}
          description={`${formatDate(from)} — ${formatDate(to)}`}
          actions={
            <>
              <label htmlFor="tb-from" className="sr-only">
                {t.accounting.from}
              </label>
              <Input
                id="tb-from"
                type="date"
                value={from}
                onChange={(event) => setFrom(event.target.value)}
                className="w-36 py-1.5 text-xs"
              />
              <label htmlFor="tb-to" className="sr-only">
                {t.accounting.to}
              </label>
              <Input
                id="tb-to"
                type="date"
                value={to}
                onChange={(event) => setTo(event.target.value)}
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
        ) : !report.data || report.data.rows.length === 0 ? (
          <EmptyState title={t.accounting.noMovements} />
        ) : (
          <>
            {/*
              The balance check is shown, not assumed. A false here would mean
              something reached the ledger without going through the posting
              service, and an accountant needs to see that immediately.
            */}
            <div className="mb-4">
              {report.data.is_balanced ? (
                <Alert tone="success">{t.accounting.balanced}</Alert>
              ) : (
                <Alert tone="danger">
                  <strong>{t.accounting.notBalanced}</strong> —{' '}
                  {t.accounting.notBalancedHint}
                </Alert>
              )}
            </div>

            <Table>
              <thead>
                <tr>
                  <Th>{t.accounting.code}</Th>
                  <Th>{t.accounting.name}</Th>
                  <Th numeric>{t.accounting.opening} {t.accounting.debit}</Th>
                  <Th numeric>{t.accounting.opening} {t.accounting.credit}</Th>
                  <Th numeric>{t.accounting.movement} {t.accounting.debit}</Th>
                  <Th numeric>{t.accounting.movement} {t.accounting.credit}</Th>
                  <Th numeric>{t.accounting.closing} {t.accounting.debit}</Th>
                  <Th numeric>{t.accounting.closing} {t.accounting.credit}</Th>
                </tr>
              </thead>
              <tbody>
                {report.data.rows.map((row) => (
                  <tr key={row.account_id}>
                    <Td>
                      <button
                        type="button"
                        className="font-mono text-xs underline underline-offset-2"
                        onClick={() => setStatementFor(row.account_id)}
                      >
                        {row.account_code}
                      </button>
                    </Td>
                    <Td>{row.account_name}</Td>
                    <Td numeric>{amount(row.opening_debit)}</Td>
                    <Td numeric>{amount(row.opening_credit)}</Td>
                    <Td numeric>{amount(row.period_debit)}</Td>
                    <Td numeric>{amount(row.period_credit)}</Td>
                    <Td numeric>{amount(row.closing_debit)}</Td>
                    <Td numeric>{amount(row.closing_credit)}</Td>
                  </tr>
                ))}
                <tr>
                  <Td className="font-semibold">{t.accounting.total}</Td>
                  <Td />
                  <Td numeric className="font-semibold">
                    {report.data.total_opening_debit}
                  </Td>
                  <Td numeric className="font-semibold">
                    {report.data.total_opening_credit}
                  </Td>
                  <Td numeric className="font-semibold">
                    {report.data.total_period_debit}
                  </Td>
                  <Td numeric className="font-semibold">
                    {report.data.total_period_credit}
                  </Td>
                  <Td numeric className="font-semibold">
                    {report.data.total_closing_debit}
                  </Td>
                  <Td numeric className="font-semibold">
                    {report.data.total_closing_credit}
                  </Td>
                </tr>
              </tbody>
            </Table>
          </>
        )}
      </Card>

      <StatementDialog
        accountId={statementFor}
        from={from}
        to={to}
        onClose={() => setStatementFor(null)}
      />
    </>
  )
}

function StatementDialog({
  accountId,
  from,
  to,
  onClose,
}: {
  accountId: string | null
  from: string
  to: string
  onClose: () => void
}) {
  const { t, formatDate } = useI18n()
  const statement = useAccountStatement(accountId, from, to)

  return (
    <Dialog
      open={accountId !== null}
      onClose={onClose}
      title={
        statement.data
          ? `${statement.data.account_code} · ${statement.data.account_name}`
          : t.accounting.statement
      }
      closeLabel={t.common.close}
      size="lg"
      footer={<Button onClick={onClose}>{t.common.close}</Button>}
    >
      {statement.isLoading ? (
        <LoadingState label={t.common.loading} />
      ) : statement.data ? (
        <div className="flex flex-col gap-3">
          <dl className="grid grid-cols-2 gap-2 text-sm">
            <dt className="text-[var(--text-secondary)]">{t.accounting.opening}</dt>
            <dd className="tabular text-right">
              {statement.data.opening_balance}
            </dd>
            <dt className="text-[var(--text-secondary)]">{t.accounting.closing}</dt>
            <dd className="tabular text-right font-medium">
              {statement.data.closing_balance}
            </dd>
          </dl>

          {statement.data.lines.length === 0 ? (
            <EmptyState title={t.accounting.noMovements} />
          ) : (
            <Table>
              <thead>
                <tr>
                  <Th>{t.accounting.entryDate}</Th>
                  <Th>{t.accounting.entryNumber}</Th>
                  <Th numeric>{t.accounting.debit}</Th>
                  <Th numeric>{t.accounting.credit}</Th>
                  <Th numeric>{t.accounting.runningBalance}</Th>
                </tr>
              </thead>
              <tbody>
                {statement.data.lines.map((line, index) => (
                  <tr key={`${line.entry_id}-${index}`}>
                    <Td>{formatDate(line.entry_date)}</Td>
                    <Td>
                      <span className="font-mono text-xs">{line.entry_number}</span>
                      {line.is_reversal ? (
                        <span className="ml-1.5">
                          <Badge tone="brand">{t.accounting.reversal}</Badge>
                        </span>
                      ) : null}
                      {line.reversed ? (
                        <span className="ml-1.5">
                          <Badge tone="warning">{t.accounting.reversed}</Badge>
                        </span>
                      ) : null}
                    </Td>
                    <Td numeric>{amount(line.debit)}</Td>
                    <Td numeric>{amount(line.credit)}</Td>
                    <Td numeric>{line.running_balance}</Td>
                  </tr>
                ))}
              </tbody>
            </Table>
          )}
        </div>
      ) : null}
    </Dialog>
  )
}
