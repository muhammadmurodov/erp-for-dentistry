import { useState } from 'react'
import { useI18n } from '@/i18n'
import { api } from '@/lib/api'
import { useAccountStatement } from '@/lib/queries'
import { describeError } from '@/lib/errorMessage'
import {
  Alert,
  Badge,
  Button,
  Dialog,
  LoadingState,
  Select,
  Table,
  Td,
  Th,
} from '@/components/ui'
import { todayInClinic } from '@/lib/clinicDate'
import { periodWindow, type DateWindow, type PeriodKey } from '@/lib/reports'

/**
 * The pieces every report screen shares: choosing a period, downloading the
 * spreadsheet, printing, and drilling from a line into the entries behind it.
 */

export const PERIOD_KEYS: PeriodKey[] = [
  'this_month',
  'last_month',
  'this_quarter',
  'year_to_date',
  'last_year',
]

export function useReportPeriod(initial: PeriodKey = 'year_to_date') {
  const today = todayInClinic()
  const [key, setKey] = useState<PeriodKey>(initial)
  const [custom, setCustom] = useState<DateWindow | null>(null)
  const [compare, setCompare] = useState(false)
  const window = custom ?? periodWindow(key, today)
  return { today, key, setKey, custom, setCustom, compare, setCompare, window }
}

export function PeriodPicker({
  period,
}: {
  period: ReturnType<typeof useReportPeriod>
}) {
  const { t } = useI18n()
  return (
    <div className="flex flex-wrap items-end gap-3">
      <label className="text-sm">
        <span className="mb-1 block text-[var(--text-muted)]">{t.reports.period}</span>
        <Select
          value={period.custom ? 'custom' : period.key}
          aria-label={t.reports.period}
          onChange={(event) => {
            if (event.target.value === 'custom') {
              period.setCustom(period.window)
              return
            }
            period.setCustom(null)
            period.setKey(event.target.value as PeriodKey)
          }}
          className="w-48"
        >
          {PERIOD_KEYS.map((key) => (
            <option key={key} value={key}>
              {t.reports.periods[key]}
            </option>
          ))}
          <option value="custom">{t.reports.periods.custom}</option>
        </Select>
      </label>
      <label className="text-sm">
        <span className="mb-1 block text-[var(--text-muted)]">{t.reports.from}</span>
        <input
          type="date"
          aria-label={t.reports.from}
          value={period.window.from}
          onChange={(event) =>
            period.setCustom({ from: event.target.value, to: period.window.to })
          }
          className="rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3 py-2"
        />
      </label>
      <label className="text-sm">
        <span className="mb-1 block text-[var(--text-muted)]">{t.reports.to}</span>
        <input
          type="date"
          aria-label={t.reports.to}
          value={period.window.to}
          onChange={(event) =>
            period.setCustom({ from: period.window.from, to: event.target.value })
          }
          className="rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3 py-2"
        />
      </label>
      <label className="flex items-center gap-2 pb-2 text-sm">
        <input
          type="checkbox"
          checked={period.compare}
          onChange={(event) => period.setCompare(event.target.checked)}
        />
        {t.reports.compareWithLastYear}
      </label>
    </div>
  )
}

/** A date on its own, for the balance sheet. */
export function AsOfPicker({
  asOf,
  onChange,
  compare,
  onCompareChange,
}: {
  asOf: string
  onChange: (value: string) => void
  compare: boolean
  onCompareChange: (value: boolean) => void
}) {
  const { t } = useI18n()
  return (
    <div className="flex flex-wrap items-end gap-3">
      <label className="text-sm">
        <span className="mb-1 block text-[var(--text-muted)]">{t.reports.asOf}</span>
        <input
          type="date"
          aria-label={t.reports.asOf}
          value={asOf}
          onChange={(event) => onChange(event.target.value)}
          className="rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3 py-2"
        />
      </label>
      <label className="flex items-center gap-2 pb-2 text-sm">
        <input
          type="checkbox"
          checked={compare}
          onChange={(event) => onCompareChange(event.target.checked)}
        />
        {t.reports.compareWithLastYear}
      </label>
    </div>
  )
}

/**
 * Download and print.
 *
 * The spreadsheet comes from the server, so the file holds the same figures
 * the screen does. The PDF is the browser's own print-to-PDF over a print
 * stylesheet — a real answer rather than a second layout engine (ADR-059).
 */
export function ExportButtons({ path, params }: { path: string; params: Record<string, string> }) {
  const { t } = useI18n()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function download() {
    setBusy(true)
    setError(null)
    try {
      const blob = await api.getBlob(path, params)
      const url = URL.createObjectURL(blob)
      const anchor = document.createElement('a')
      anchor.href = url
      anchor.download = `${path.split('/')[1] ?? 'report'}.xlsx`
      document.body.appendChild(anchor)
      anchor.click()
      anchor.remove()
      URL.revokeObjectURL(url)
    } catch (caught) {
      setError(describeError(caught, t))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex flex-wrap items-center gap-2 print:hidden">
      <Button size="sm" onClick={() => void download()} disabled={busy}>
        {t.reports.exportExcel}
      </Button>
      <Button size="sm" onClick={() => window.print()}>
        {t.reports.print}
      </Button>
      {error ? <Alert tone="danger">{error}</Alert> : null}
    </div>
  )
}

/** The footnotes a statement must carry rather than imply. */
export function StatementNotes({
  unconfirmed,
  extra,
}: {
  unconfirmed: number
  extra?: string
}) {
  const { t } = useI18n()
  return (
    <div className="space-y-2 px-4 pb-4 text-sm">
      <Alert tone="info">{t.reports.managementOnly}</Alert>
      {unconfirmed > 0 ? (
        <Alert tone="warning">
          {t.reports.unconfirmedAccounts.replace('{count}', String(unconfirmed))}
        </Alert>
      ) : null}
      {extra ? <Alert tone="info">{extra}</Alert> : null}
    </div>
  )
}

/**
 * The entries behind a figure.
 *
 * Reached from any statement line, over the same window the report covers,
 * and behind the same permission — a reader who may see the total may see
 * what makes it up.
 */
export function DrillDown({
  accountId,
  accountName,
  window,
  onClose,
}: {
  accountId: string
  accountName: string
  window: DateWindow
  onClose: () => void
}) {
  const { t, formatMoney, formatDate } = useI18n()
  const statement = useAccountStatement(accountId, window.from, window.to)

  return (
    <Dialog
      open
      size="lg"
      onClose={onClose}
      title={`${accountName} · ${window.from} — ${window.to}`}
      closeLabel={t.common.close}
      footer={<Button onClick={onClose}>{t.common.close}</Button>}
    >
      {statement.isLoading ? (
        <LoadingState label={t.common.loading} />
      ) : statement.isError || !statement.data ? (
        <Alert tone="danger">{describeError(statement.error, t)}</Alert>
      ) : statement.data.lines.length === 0 ? (
        <Alert tone="info">{t.reports.noEntries}</Alert>
      ) : (
        <Table>
          <thead>
            <tr>
              <Th>{t.reports.date}</Th>
              <Th>{t.reports.entry}</Th>
              <Th>{t.reports.description}</Th>
              <Th numeric>{t.reports.debit}</Th>
              <Th numeric>{t.reports.credit}</Th>
              <Th numeric>{t.reports.runningBalance}</Th>
            </tr>
          </thead>
          <tbody>
            {statement.data.lines.map((line, index) => (
              <tr key={`${line.entry_id}-${index}`}>
                <Td>{formatDate(line.entry_date)}</Td>
                <Td>
                  <span className="font-mono text-xs">{line.entry_number}</span>
                  {line.reversed ? (
                    <span className="ml-2">
                      <Badge tone="warning">{t.reports.reversed}</Badge>
                    </span>
                  ) : null}
                </Td>
                <Td>{line.line_description || line.description}</Td>
                <Td numeric>{formatMoney(line.debit)}</Td>
                <Td numeric>{formatMoney(line.credit)}</Td>
                <Td numeric>{formatMoney(line.running_balance)}</Td>
              </tr>
            ))}
          </tbody>
        </Table>
      )}
    </Dialog>
  )
}
