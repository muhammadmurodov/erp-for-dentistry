import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useI18n } from '@/i18n'
import { useAuth } from '@/features/auth/AuthProvider'
import { api } from '@/lib/api'
import { useJournal, useJournalEntry, usePeriods } from '@/lib/queries'
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
  Field,
  Input,
  LoadingState,
  Table,
  Td,
  Th,
} from '@/components/ui'
import { JournalEntryDialog } from './JournalEntryDialog'
import type { JournalEntry, JournalEntryDetail } from '@/lib/types'

export function JournalList() {
  const { t, formatDate } = useI18n()
  const { can } = useAuth()
  const queryClient = useQueryClient()

  const mayRead = can('accounting.journal.read')
  const mayDraft = can('accounting.journal.draft')
  const mayPost = can('accounting.journal.post')
  const mayReverse = can('accounting.journal.reverse')

  const entries = useJournal(mayRead)
  const periods = usePeriods(can('organization.period.read'))

  const [createOpen, setCreateOpen] = useState(false)
  const [detailId, setDetailId] = useState<string | null>(null)
  const [reverseTarget, setReverseTarget] = useState<JournalEntry | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const invalidate = () =>
    void queryClient.invalidateQueries({ queryKey: ['accounting'] })

  const post = useMutation({
    mutationFn: (id: string) =>
      api.post<JournalEntryDetail>(`/accounting/journal/${id}/post`),
    onSuccess: (entry) => {
      setError(null)
      setNotice(`${t.accounting.entryPosted} ${entry.entry_number}`)
      invalidate()
    },
    onError: (err) => setError(describeError(err, t)),
  })

  if (!mayRead) return <Alert tone="warning">{t.moduleStatus.noAccess}</Alert>
  if (entries.isLoading) return <LoadingState label={t.common.loading} />
  if (entries.isError) {
    return (
      <ErrorState
        title={t.errors.loadFailedTitle}
        message={describeError(entries.error, t)}
        requestId={errorRequestId(entries.error)}
        requestIdLabel={t.common.requestId}
        onRetry={() => void entries.refetch()}
        retryLabel={t.common.retry}
      />
    )
  }

  const rows = entries.data ?? []
  const hasPeriods = (periods.data ?? []).length > 0

  return (
    <>
      <Card>
        <CardHeader
          title={t.accounting.journalTab}
          actions={
            mayDraft && hasPeriods ? (
              <Button variant="primary" size="sm" onClick={() => setCreateOpen(true)}>
                {t.accounting.newEntry}
              </Button>
            ) : undefined
          }
        />

        {notice ? (
          <div className="mb-4">
            <Alert tone="success">{notice}</Alert>
          </div>
        ) : null}
        {error ? (
          <div className="mb-4">
            <Alert tone="danger">{error}</Alert>
          </div>
        ) : null}

        {/* Posting needs a period to post into. Say so rather than letting
            the attempt fail with a validation error. */}
        {!hasPeriods ? (
          <div className="mb-4">
            <Alert tone="warning">
              {t.accounting.noPeriodsTitle} — {t.accounting.noPeriodsBody}
            </Alert>
          </div>
        ) : null}

        {rows.length === 0 ? (
          <EmptyState
            title={t.accounting.emptyJournalTitle}
            body={hasPeriods ? t.accounting.emptyJournalBody : t.accounting.noPeriodsBody}
          />
        ) : (
          <Table>
            <thead>
              <tr>
                <Th>{t.accounting.entryNumber}</Th>
                <Th>{t.accounting.entryDate}</Th>
                <Th>{t.accounting.description}</Th>
                <Th numeric>{t.accounting.total}</Th>
                <Th>{t.accounting.status}</Th>
                <Th>{t.common.actions}</Th>
              </tr>
            </thead>
            <tbody>
              {rows.map((entry) => (
                <tr key={entry.id}>
                  <Td>
                    <button
                      type="button"
                      className="font-mono text-xs underline underline-offset-2"
                      onClick={() => setDetailId(entry.id)}
                    >
                      {entry.entry_number}
                    </button>
                  </Td>
                  <Td>{formatDate(entry.entry_date)}</Td>
                  <Td>{entry.description}</Td>
                  <Td numeric>{entry.total}</Td>
                  <Td>
                    <EntryStatusBadge entry={entry} />
                  </Td>
                  <Td>
                    <div className="flex flex-wrap gap-1.5">
                      {entry.status === 'DRAFT' && mayPost ? (
                        <Button
                          size="sm"
                          variant="ghost"
                          loading={post.isPending}
                          onClick={() => post.mutate(entry.id)}
                        >
                          {t.accounting.post}
                        </Button>
                      ) : null}
                      {entry.status === 'POSTED' &&
                      mayReverse &&
                      !entry.reversed_by_entry_id ? (
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => {
                            setError(null)
                            setReverseTarget(entry)
                          }}
                        >
                          {t.accounting.reverse}
                        </Button>
                      ) : null}
                    </div>
                  </Td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </Card>

      <JournalEntryDialog
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onSaved={(message) => {
          setCreateOpen(false)
          setError(null)
          setNotice(message)
        }}
      />

      <EntryDetailDialog id={detailId} onClose={() => setDetailId(null)} />

      <ReverseDialog
        entry={reverseTarget}
        onClose={() => setReverseTarget(null)}
        onDone={(message) => {
          setReverseTarget(null)
          setNotice(message)
          invalidate()
        }}
      />
    </>
  )
}

function EntryStatusBadge({ entry }: { entry: JournalEntry }) {
  const { t } = useI18n()
  if (entry.reversal_of_entry_id) {
    return <Badge tone="brand">{t.accounting.reversal}</Badge>
  }
  if (entry.reversed_by_entry_id) {
    return <Badge tone="warning">{t.accounting.reversed}</Badge>
  }
  if (entry.status === 'POSTED') {
    return <Badge tone="success">{t.accounting.posted}</Badge>
  }
  return <Badge>{t.accounting.draft}</Badge>
}

function EntryDetailDialog({
  id,
  onClose,
}: {
  id: string | null
  onClose: () => void
}) {
  const { t, formatDate } = useI18n()
  const entry = useJournalEntry(id)

  return (
    <Dialog
      open={id !== null}
      onClose={onClose}
      title={entry.data ? entry.data.entry_number : t.common.loading}
      closeLabel={t.common.close}
      size="lg"
      footer={<Button onClick={onClose}>{t.common.close}</Button>}
    >
      {entry.isLoading ? (
        <LoadingState label={t.common.loading} />
      ) : entry.data ? (
        <div className="flex flex-col gap-3">
          <dl className="grid grid-cols-[8rem_1fr] gap-x-4 gap-y-1 text-sm">
            <dt className="text-[var(--text-secondary)]">{t.accounting.entryDate}</dt>
            <dd>{formatDate(entry.data.entry_date)}</dd>
            <dt className="text-[var(--text-secondary)]">
              {t.accounting.description}
            </dt>
            <dd>{entry.data.description}</dd>
            {entry.data.reason ? (
              <>
                <dt className="text-[var(--text-secondary)]">
                  {t.accounting.reason}
                </dt>
                <dd>{entry.data.reason}</dd>
              </>
            ) : null}
          </dl>

          <Table>
            <thead>
              <tr>
                <Th>{t.accounting.account}</Th>
                <Th numeric>{t.accounting.debit}</Th>
                <Th numeric>{t.accounting.credit}</Th>
              </tr>
            </thead>
            <tbody>
              {entry.data.lines.map((line) => (
                <tr key={line.id}>
                  <Td>
                    <span className="font-mono text-xs">{line.account_code}</span>{' '}
                    {line.account_name}
                  </Td>
                  <Td numeric>{line.debit === '0.00' ? '' : line.debit}</Td>
                  <Td numeric>{line.credit === '0.00' ? '' : line.credit}</Td>
                </tr>
              ))}
              <tr>
                <Td className="font-medium">{t.accounting.total}</Td>
                <Td numeric className="font-medium">
                  {entry.data.total}
                </Td>
                <Td numeric className="font-medium">
                  {entry.data.total}
                </Td>
              </tr>
            </tbody>
          </Table>
        </div>
      ) : null}
    </Dialog>
  )
}

function ReverseDialog({
  entry,
  onClose,
  onDone,
}: {
  entry: JournalEntry | null
  onClose: () => void
  onDone: (message: string) => void
}) {
  const { t } = useI18n()
  const [reason, setReason] = useState('')
  const [error, setError] = useState<string | null>(null)

  const reverse = useMutation({
    mutationFn: () =>
      api.post<JournalEntryDetail>(`/accounting/journal/${entry?.id}/reverse`, {
        reason,
      }),
    onSuccess: (reversal) => {
      setReason('')
      setError(null)
      onDone(`${t.accounting.entryReversed} ${reversal.entry_number}`)
    },
    onError: (err) => setError(describeError(err, t)),
  })

  return (
    <Dialog
      open={entry !== null}
      onClose={onClose}
      title={`${t.accounting.reverseTitle}: ${entry?.entry_number ?? ''}`}
      closeLabel={t.common.close}
      footer={
        <>
          <Button onClick={onClose} disabled={reverse.isPending}>
            {t.common.cancel}
          </Button>
          <Button
            variant="danger"
            loading={reverse.isPending}
            disabled={!reason.trim()}
            onClick={() => {
              setError(null)
              reverse.mutate()
            }}
          >
            {t.accounting.reverse}
          </Button>
        </>
      }
    >
      {error ? (
        <div className="mb-4">
          <Alert tone="danger">{error}</Alert>
        </div>
      ) : null}
      <p className="mb-4 text-sm text-[var(--text-secondary)]">
        {t.accounting.reverseBody}
      </p>
      <Field
        label={t.accounting.reason}
        htmlFor="reverse-reason"
        required
        hint={t.accounting.reverseReasonRequired}
      >
        <Input
          id="reverse-reason"
          value={reason}
          onChange={(event) => setReason(event.target.value)}
        />
      </Field>
    </Dialog>
  )
}
