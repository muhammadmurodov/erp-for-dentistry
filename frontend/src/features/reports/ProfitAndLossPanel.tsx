import { useState } from 'react'
import { useI18n } from '@/i18n'
import { useProfitAndLoss } from '@/lib/queries'
import { describeError, errorRequestId } from '@/lib/errorMessage'
import {
  Badge,
  Card,
  CardHeader,
  ErrorState,
  LoadingState,
  Table,
  Td,
  Th,
} from '@/components/ui'
import {
  amountByCode,
  comparativeWindow,
  mergedCodes,
  sectionIsEmpty,
  variance,
  variancePercent,
} from '@/lib/reports'
import type { StatementSection } from '@/lib/types'
import { DrillDown, ExportButtons, PeriodPicker, StatementNotes, useReportPeriod } from './shared'

/** What was earned and what it cost, over a period the reader chooses. */
export function ProfitAndLossPanel() {
  const { t, formatMoney } = useI18n()
  const period = useReportPeriod('year_to_date')
  const [drill, setDrill] = useState<{ id: string; name: string } | null>(null)

  const compare = period.compare ? comparativeWindow(period.window) : null
  const report = useProfitAndLoss(
    {
      from_date: period.window.from,
      to_date: period.window.to,
      ...(compare ? { compare_from: compare.from, compare_to: compare.to } : {}),
    },
    true,
  )

  return (
    <Card>
      <CardHeader
        title={t.reports.profitAndLossTab}
        actions={
          <ExportButtons
            path="/reporting/profit-and-loss/export"
            params={{
              from_date: period.window.from,
              to_date: period.window.to,
              ...(compare ? { compare_from: compare.from, compare_to: compare.to } : {}),
            }}
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
      ) : (
        <>
          <p className="px-4 text-sm text-[var(--text-muted)]">
            {report.data.from_date} — {report.data.to_date}
            {report.data.comparative
              ? ` · ${t.reports.comparedWith} ${report.data.comparative.from_date} — ${report.data.comparative.to_date}`
              : ''}
          </p>
          <StatementNotes unconfirmed={report.data.accounts_needing_confirmation} />
          <Table>
            <thead>
              <tr>
                <Th>{t.reports.account}</Th>
                <Th numeric>{t.reports.amount}</Th>
                {report.data.comparative ? <Th numeric>{t.reports.comparative}</Th> : null}
                {report.data.comparative ? <Th numeric>{t.reports.change}</Th> : null}
              </tr>
            </thead>
            <tbody>
              <Section
                label={t.reports.revenue}
                section={report.data.revenue}
                prior={report.data.comparative?.revenue}
                onDrill={setDrill}
              />
              <Section
                label={t.reports.expenses}
                section={report.data.expenses}
                prior={report.data.comparative?.expenses}
                onDrill={setDrill}
              />
              <Section
                label={t.reports.unclassified}
                section={report.data.unclassified}
                prior={report.data.comparative?.unclassified}
                onDrill={setDrill}
                hint={t.reports.unclassifiedHint}
              />
              <tr className="border-t-2 border-[var(--border)]">
                <Td>
                  <strong>{t.reports.netResult}</strong>
                </Td>
                <Td numeric>
                  <strong>{formatMoney(report.data.net_result)}</strong>
                </Td>
                {report.data.comparative ? (
                  <Td numeric>{formatMoney(report.data.comparative.net_result)}</Td>
                ) : null}
                {report.data.comparative ? (
                  <Td numeric>
                    {formatMoney(
                      variance(report.data.net_result, report.data.comparative.net_result),
                    )}
                  </Td>
                ) : null}
              </tr>
            </tbody>
          </Table>
        </>
      )}

      {drill ? (
        <DrillDown
          accountId={drill.id}
          accountName={drill.name}
          window={period.window}
          onClose={() => setDrill(null)}
        />
      ) : null}
    </Card>
  )
}

function Section({
  label,
  section,
  prior,
  onDrill,
  hint,
}: {
  label: string
  section: StatementSection
  prior: StatementSection | undefined
  onDrill: (line: { id: string; name: string }) => void
  hint?: string
}) {
  const { t, formatMoney } = useI18n()
  if (sectionIsEmpty(section, prior)) return null
  const codes = mergedCodes(section, prior)
  const byCode = new Map(section.lines.map((line) => [line.account_code, line]))

  return (
    <>
      <tr>
        <Td colSpan={prior ? 4 : 2}>
          <strong>{label}</strong>
          {hint ? (
            <span className="ml-2 text-xs text-[var(--text-muted)]">{hint}</span>
          ) : null}
        </Td>
      </tr>
      {codes.map((code) => {
        const line = byCode.get(code)
        const amount = line?.amount ?? '0.00'
        const was = amountByCode(prior, code)
        const percent = prior ? variancePercent(amount, was) : null
        return (
          <tr key={code}>
            <Td>
              {line ? (
                <button
                  type="button"
                  className="text-left underline decoration-dotted underline-offset-2 hover:text-[var(--accent)] print:no-underline"
                  onClick={() => onDrill({ id: line.account_id, name: line.account_name })}
                >
                  <span className="font-mono text-xs">{code}</span> {line.account_name}
                </button>
              ) : (
                <>
                  <span className="font-mono text-xs">{code}</span>{' '}
                  {prior?.lines.find((l) => l.account_code === code)?.account_name}
                </>
              )}
              {line?.needs_confirmation ? (
                <span className="ml-2">
                  <Badge tone="warning">{t.reports.unconfirmed}</Badge>
                </span>
              ) : null}
            </Td>
            <Td numeric>{formatMoney(amount)}</Td>
            {prior ? <Td numeric>{formatMoney(was)}</Td> : null}
            {prior ? (
              <Td numeric>
                {formatMoney(variance(amount, was))}
                {percent !== null ? (
                  <span className="ml-1 text-xs text-[var(--text-muted)]">
                    ({percent > 0 ? '+' : ''}
                    {percent}%)
                  </span>
                ) : null}
              </Td>
            ) : null}
          </tr>
        )
      })}
      <tr>
        <Td>
          <strong>
            {t.reports.total} {label.toLowerCase()}
          </strong>
        </Td>
        <Td numeric>
          <strong>{formatMoney(section.total)}</strong>
        </Td>
        {prior ? <Td numeric>{formatMoney(prior.total)}</Td> : null}
        {prior ? <Td numeric>{formatMoney(variance(section.total, prior.total))}</Td> : null}
      </tr>
    </>
  )
}
