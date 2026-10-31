import { useState } from 'react'
import { useI18n } from '@/i18n'
import { useAuth } from '@/features/auth/AuthProvider'
import { useAuditLogs } from '@/lib/queries'
import { describeError, errorRequestId } from '@/lib/errorMessage'
import {
  Alert,
  Badge,
  Button,
  Card,
  CardHeader,
  EmptyState,
  ErrorState,
  Input,
  LoadingState,
  Select,
  Table,
  Td,
  Th,
} from '@/components/ui'
import type { AuditLogEntry } from '@/lib/types'

export function AuditSettings() {
  const { t, formatDateTime } = useI18n()
  const { can } = useAuth()
  const mayRead = can('audit.log.read')

  const [actionFilter, setActionFilter] = useState('')
  const [outcomeFilter, setOutcomeFilter] = useState('')
  const [before, setBefore] = useState<string | undefined>(undefined)
  const [expanded, setExpanded] = useState<string | null>(null)

  const logs = useAuditLogs(
    {
      action: actionFilter || undefined,
      outcome: outcomeFilter || undefined,
      before,
    },
    mayRead,
  )

  if (!mayRead) return <Alert tone="warning">{t.moduleStatus.noAccess}</Alert>
  if (logs.isLoading) return <LoadingState label={t.common.loading} />
  if (logs.isError) {
    return (
      <ErrorState
        title={t.errors.loadFailedTitle}
        message={describeError(logs.error, t)}
        requestId={errorRequestId(logs.error)}
        requestIdLabel={t.common.requestId}
        onRetry={() => void logs.refetch()}
        retryLabel={t.common.retry}
      />
    )
  }

  const items = logs.data?.items ?? []

  return (
    <Card>
      <CardHeader
        title={t.audit.title}
        description={t.audit.subtitle}
        actions={
          <>
            <label htmlFor="audit-action" className="sr-only">
              {t.audit.action}
            </label>
            <Input
              id="audit-action"
              value={actionFilter}
              onChange={(event) => {
                setActionFilter(event.target.value)
                setBefore(undefined)
              }}
              placeholder={t.audit.action}
              className="w-48 py-1.5 text-xs"
            />
            <label htmlFor="audit-outcome" className="sr-only">
              {t.audit.outcome}
            </label>
            <Select
              id="audit-outcome"
              value={outcomeFilter}
              onChange={(event) => {
                setOutcomeFilter(event.target.value)
                setBefore(undefined)
              }}
              className="w-auto py-1.5 text-xs"
            >
              <option value="">{t.common.filter}</option>
              <option value="success">{t.audit.success}</option>
              <option value="failure">{t.audit.failure}</option>
              <option value="denied">{t.audit.denied}</option>
            </Select>
            {actionFilter || outcomeFilter || before ? (
              <Button
                size="sm"
                variant="ghost"
                onClick={() => {
                  setActionFilter('')
                  setOutcomeFilter('')
                  setBefore(undefined)
                }}
              >
                {t.common.clear}
              </Button>
            ) : null}
          </>
        }
      />

      {items.length === 0 ? (
        <EmptyState title={t.audit.emptyTitle} body={t.audit.emptyBody} />
      ) : (
        <>
          <Table>
            <thead>
              <tr>
                <Th>{t.audit.occurredAt}</Th>
                <Th>{t.audit.actor}</Th>
                <Th>{t.audit.action}</Th>
                <Th>{t.audit.target}</Th>
                <Th>{t.audit.outcome}</Th>
                <Th>{t.audit.changes}</Th>
              </tr>
            </thead>
            <tbody>
              {items.map((entry) => (
                <AuditRow
                  key={entry.id}
                  entry={entry}
                  expanded={expanded === entry.id}
                  onToggle={() =>
                    setExpanded(expanded === entry.id ? null : entry.id)
                  }
                  formatDateTime={formatDateTime}
                />
              ))}
            </tbody>
          </Table>

          {logs.data?.next_before ? (
            <div className="mt-4 flex justify-center">
              <Button
                size="sm"
                onClick={() => setBefore(logs.data?.next_before ?? undefined)}
              >
                {t.audit.loadMore}
              </Button>
            </div>
          ) : null}
        </>
      )}
    </Card>
  )
}

function AuditRow({
  entry,
  expanded,
  onToggle,
  formatDateTime,
}: {
  entry: AuditLogEntry
  expanded: boolean
  onToggle: () => void
  formatDateTime: (value: string | null | undefined) => string
}) {
  const { t } = useI18n()
  const hasDetail =
    entry.old_values !== null || entry.new_values !== null || entry.reason

  const tone =
    entry.outcome === 'success'
      ? 'success'
      : entry.outcome === 'denied'
        ? 'warning'
        : 'danger'
  const label =
    entry.outcome === 'success'
      ? t.audit.success
      : entry.outcome === 'denied'
        ? t.audit.denied
        : t.audit.failure

  return (
    <>
      <tr>
        <Td className="whitespace-nowrap">{formatDateTime(entry.occurred_at)}</Td>
        <Td>
          <span className="font-mono text-xs">{entry.actor_label}</span>
        </Td>
        <Td>
          <span className="font-mono text-xs">{entry.action}</span>
        </Td>
        <Td>
          {entry.target_label ?? entry.target_type ?? '—'}
          {entry.target_id ? (
            <span className="block font-mono text-[10px] text-[var(--text-muted)]">
              {entry.target_id}
            </span>
          ) : null}
        </Td>
        <Td>
          <Badge tone={tone}>{label}</Badge>
        </Td>
        <Td>
          {hasDetail ? (
            <Button
              size="sm"
              variant="ghost"
              aria-expanded={expanded}
              onClick={onToggle}
            >
              {expanded ? '▲' : '▼'}
            </Button>
          ) : (
            <span className="text-xs text-[var(--text-muted)]">—</span>
          )}
        </Td>
      </tr>
      {expanded ? (
        <tr>
          <Td className="bg-[var(--surface-sunken)]" />
          <td
            colSpan={5}
            className="border-b border-[var(--border-subtle)] bg-[var(--surface-sunken)] px-3 py-3"
          >
            {entry.reason ? (
              <p className="mb-2 text-xs">
                <span className="text-[var(--text-secondary)]">
                  {t.audit.reason}:{' '}
                </span>
                {entry.reason}
              </p>
            ) : null}
            <div className="grid gap-3 sm:grid-cols-2">
              <ValueBlock label={t.audit.before} values={entry.old_values} />
              <ValueBlock label={t.audit.after} values={entry.new_values} />
            </div>
            {entry.request_id ? (
              <p className="mt-2 font-mono text-[10px] text-[var(--text-muted)]">
                {t.common.requestId}: {entry.request_id}
                {entry.ip_address ? ` · ${entry.ip_address}` : ''}
              </p>
            ) : null}
          </td>
        </tr>
      ) : null}
    </>
  )
}

function ValueBlock({
  label,
  values,
}: {
  label: string
  values: Record<string, unknown> | null
}) {
  if (!values || Object.keys(values).length === 0) return null
  return (
    <div>
      <p className="mb-1 text-xs font-semibold text-[var(--text-secondary)]">
        {label}
      </p>
      <dl className="flex flex-col gap-0.5">
        {Object.entries(values).map(([key, value]) => (
          <div key={key} className="flex gap-2 text-xs">
            <dt className="font-mono text-[var(--text-muted)]">{key}</dt>
            <dd className="min-w-0 break-all font-mono text-[var(--text-primary)]">
              {value === null ? '—' : String(value)}
            </dd>
          </div>
        ))}
      </dl>
    </div>
  )
}
