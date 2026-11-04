import { useState } from 'react'
import { useI18n } from '@/i18n'
import { useBalanceSheet } from '@/lib/queries'
import { describeError, errorRequestId } from '@/lib/errorMessage'
import {
  Alert,
  Badge,
  Card,
  CardHeader,
  ErrorState,
  LoadingState,
  Table,
  Td,
  Th,
} from '@/components/ui'
import { todayInClinic } from '@/lib/clinicDate'
import {
  amountByCode,
  balanceSheetWarning,
  comparativeDate,
  mergedCodes,
  sectionIsEmpty,
  variance,
} from '@/lib/reports'
import type { StatementSection } from '@/lib/types'
import { AsOfPicker, DrillDown, ExportButtons, StatementNotes } from './shared'

/**
 * What the clinic owns and owes on one day.
 *
 * If it does not balance, the screen says so in the loudest way it has. A
 * balance sheet that quietly hides a difference is worse than no balance
 * sheet, because somebody will act on it.
 */
export function BalanceSheetPanel() {
  const { t, formatMoney } = useI18n()
  const [asOf, setAsOf] = useState(todayInClinic())
  const [compare, setCompare] = useState(false)
  const [drill, setDrill] = useState<{ id: string; name: string } | null>(null)

  const report = useBalanceSheet(
    { as_of: asOf, ...(compare ? { compare_as_of: comparativeDate(asOf) } : {}) },
    true,
  )
  const data = report.data
  const window = { from: data?.fiscal_year_starts_on ?? `${asOf.slice(0, 4)}-01-01`, to: asOf }

  return (
    <Card>
      <CardHeader
        title={t.reports.balanceSheetTab}
        actions={
          <ExportButtons
            path="/reporting/balance-sheet/export"
            params={{
              as_of: asOf,
              ...(compare ? { compare_as_of: comparativeDate(asOf) } : {}),
            }}
          />
        }
      />
      <div className="px-4 pb-4 print:hidden">
        <AsOfPicker
          asOf={asOf}
          onChange={setAsOf}
          compare={compare}
          onCompareChange={setCompare}
        />
      </div>

      {report.isLoading ? (
        <LoadingState label={t.common.loading} />
      ) : report.isError || !data ? (
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
            {t.reports.asOf} {data.as_of}
            {data.comparative ? ` · ${t.reports.comparedWith} ${data.comparative.as_of}` : ''}
          </p>
          {balanceSheetWarning(data) === 'unbalanced' ? (
            <div className="px-4 pt-3">
              <Alert tone="danger">
                {t.reports.doesNotBalance.replace('{amount}', formatMoney(data.difference))}
              </Alert>
            </div>
          ) : null}
          <StatementNotes unconfirmed={data.accounts_needing_confirmation} />
          <Table>
            <thead>
              <tr>
                <Th>{t.reports.account}</Th>
                <Th numeric>{t.reports.amount}</Th>
                {data.comparative ? <Th numeric>{t.reports.comparative}</Th> : null}
                {data.comparative ? <Th numeric>{t.reports.change}</Th> : null}
              </tr>
            </thead>
            <tbody>
              <Section
                label={t.reports.assets}
                section={data.assets}
                prior={data.comparative?.assets}
                onDrill={setDrill}
              />
              <Section
                label={t.reports.liabilities}
                section={data.liabilities}
                prior={data.comparative?.liabilities}
                onDrill={setDrill}
              />
              <Section
                label={t.reports.equity}
                section={data.equity}
                prior={data.comparative?.equity}
                onDrill={setDrill}
              />
              <Section
                label={t.reports.unclassified}
                section={data.unclassified}
                prior={data.comparative?.unclassified}
                onDrill={setDrill}
                hint={t.reports.unclassifiedHint}
              />
              <tr>
                <Td>
                  {t.reports.resultForTheYear}
                  <span className="ml-2 text-xs text-[var(--text-muted)]">
                    {t.reports.resultNotYetClosed}
                  </span>
                </Td>
                <Td numeric>{formatMoney(data.result_for_the_year)}</Td>
                {data.comparative ? (
                  <Td numeric>{formatMoney(data.comparative.result_for_the_year)}</Td>
                ) : null}
                {data.comparative ? (
                  <Td numeric>
                    {formatMoney(
                      variance(
                        data.result_for_the_year,
                        data.comparative.result_for_the_year,
                      ),
                    )}
                  </Td>
                ) : null}
              </tr>
              <tr className="border-t-2 border-[var(--border)]">
                <Td>
                  <strong>{t.reports.totalAssets}</strong>
                </Td>
                <Td numeric>
                  <strong>{formatMoney(data.total_assets)}</strong>
                </Td>
                {data.comparative ? (
                  <Td numeric>{formatMoney(data.comparative.total_assets)}</Td>
                ) : null}
                {data.comparative ? <Td /> : null}
              </tr>
              <tr>
                <Td>
                  <strong>{t.reports.totalClaims}</strong>
                </Td>
                <Td numeric>
                  <strong>{formatMoney(data.total_liabilities_and_equity)}</strong>
                </Td>
                {data.comparative ? (
                  <Td numeric>
                    {formatMoney(data.comparative.total_liabilities_and_equity)}
                  </Td>
                ) : null}
                {data.comparative ? <Td /> : null}
              </tr>
            </tbody>
          </Table>
        </>
      )}

      {drill ? (
        <DrillDown
          accountId={drill.id}
          accountName={drill.name}
          window={window}
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
            {prior ? <Td numeric>{formatMoney(variance(amount, was))}</Td> : null}
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
