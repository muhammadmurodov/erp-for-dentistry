import { useMemo, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useI18n } from '@/i18n'
import { useAuth } from '@/features/auth/AuthProvider'
import { api } from '@/lib/api'
import { useDuplicateReviews } from '@/lib/queries'
import { describeError, errorRequestId } from '@/lib/errorMessage'
import {
  compareIdentities,
  countDifferences,
  type FieldComparison,
  type IdentityField,
} from '@/lib/patientIdentity'
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
  LoadingState,
  Textarea,
} from '@/components/ui'
import type { DuplicateReviewDetail, PatientSummary } from '@/lib/types'

/**
 * The queue of patients who might already be on file.
 *
 * The clinic has never had a patient number: the paper register identifies
 * people by family name and birth year, and the same person turns up on
 * different days as unrelated rows. So duplicates are the normal case, and
 * this screen is the one place they get settled.
 *
 * Nothing here merges on its own. Registration was not refused, both records
 * are live, and the only two outcomes are a person saying "different people"
 * or a person saying "same person" — each with a reason, each audited, and the
 * losing record kept rather than deleted, because treatments were recorded
 * against it (ADR-041).
 */
export function DuplicateReviewQueue() {
  const { t } = useI18n()
  const { can } = useAuth()
  const mayDecide = can('clinic.patient.manage')
  const reviews = useDuplicateReviews(mayDecide)
  const [openId, setOpenId] = useState<string | null>(null)

  if (!mayDecide) {
    // Reception registers and may raise these; deciding one is a separate
    // authority, and saying so beats an empty screen.
    return (
      <Card>
        <CardHeader title={t.clinic.reviewQueueTitle} />
        <Alert tone="warning">{t.clinic.reviewQueueNoAccess}</Alert>
      </Card>
    )
  }

  if (reviews.isLoading) return <LoadingState label={t.common.loading} />
  if (reviews.isError) {
    return (
      <ErrorState
        title={t.errors.loadFailedTitle}
        message={describeError(reviews.error, t)}
        requestId={errorRequestId(reviews.error)}
        requestIdLabel={t.common.requestId}
        onRetry={() => void reviews.refetch()}
        retryLabel={t.common.retry}
      />
    )
  }

  const rows = reviews.data ?? []
  const open = rows.find((row) => row.id === openId) ?? null

  return (
    <>
      <Card>
        <CardHeader
          title={t.clinic.reviewQueueTitle}
          actions={
            rows.length > 0 ? (
              <Badge tone="warning">{String(rows.length)}</Badge>
            ) : null
          }
        />
        <p className="px-4 pb-2 text-sm text-[var(--text-secondary)]">
          {t.clinic.reviewQueueHint}
        </p>
        {rows.length === 0 ? (
          <EmptyState title={t.clinic.reviewQueueEmpty} />
        ) : (
          <ul className="divide-y divide-[var(--border)]">
            {rows.map((review) => (
              <QueueRow
                key={review.id}
                review={review}
                onOpen={() => setOpenId(review.id)}
              />
            ))}
          </ul>
        )}
      </Card>
      {open ? (
        <ReviewDialog review={open} onClose={() => setOpenId(null)} />
      ) : null}
    </>
  )
}

function QueueRow({
  review,
  onOpen,
}: {
  review: DuplicateReviewDetail
  onOpen: () => void
}) {
  const { t, formatDate } = useI18n()
  const differences = useMemo(
    () => countDifferences(compareIdentities(review.patient, review.candidate)),
    [review],
  )

  return (
    <li className="flex flex-wrap items-center justify-between gap-3 px-4 py-3">
      <div className="min-w-0">
        <p className="truncate text-sm font-medium">
          {review.patient.display_name}
          <span className="text-[var(--text-secondary)]"> · </span>
          {review.candidate.display_name}
        </p>
        <p className="mt-0.5 text-xs text-[var(--text-secondary)]">
          {review.reason} · {formatDate(review.created_at)}
        </p>
      </div>
      <div className="flex shrink-0 items-center gap-2">
        {differences > 0 ? (
          <Badge tone="neutral">
            {t.clinic.reviewDifferenceCount.replace('{n}', String(differences))}
          </Badge>
        ) : null}
        <Button size="sm" onClick={onOpen}>
          {t.clinic.reviewCompare}
        </Button>
      </div>
    </li>
  )
}

/**
 * Both records side by side, and the two decisions.
 *
 * The merge is behind a second, explicit confirmation that names the record
 * being kept and the record being filed away, because a merge is the one
 * action on this screen that cannot be undone from the screen.
 */
function ReviewDialog({
  review,
  onClose,
}: {
  review: DuplicateReviewDetail
  onClose: () => void
}) {
  const { t, formatDate } = useI18n()
  const queryClient = useQueryClient()
  const [note, setNote] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [confirmMerge, setConfirmMerge] = useState(false)

  const rows = useMemo(
    () => compareIdentities(review.patient, review.candidate),
    [review],
  )
  const differences = countDifferences(rows)
  const noteTooShort = note.trim().length < 3

  const surviving =
    review.candidate.id === review.survivor_id ? review.candidate : review.patient
  const filed =
    review.candidate.id === review.survivor_id ? review.patient : review.candidate

  const resolve = useMutation({
    mutationFn: (merge: boolean) =>
      api.post(`/clinic/duplicate-reviews/${review.id}/resolve`, {
        merge,
        note: note.trim(),
      }),
    onSuccess: () => {
      setError(null)
      setConfirmMerge(false)
      void queryClient.invalidateQueries({ queryKey: ['clinic'] })
      onClose()
    },
    onError: (caught) => {
      setConfirmMerge(false)
      setError(describeError(caught, t))
    },
  })

  return (
    <>
      <Dialog
        open
        onClose={onClose}
        size="lg"
        title={t.clinic.reviewDialogTitle}
        closeLabel={t.common.close}
        footer={
          <>
            <Button onClick={onClose} disabled={resolve.isPending}>
              {t.common.cancel}
            </Button>
            <Button
              onClick={() => resolve.mutate(false)}
              disabled={noteTooShort || resolve.isPending}
              loading={resolve.isPending && !confirmMerge}
            >
              {t.clinic.reviewDifferentPeople}
            </Button>
            <Button
              variant="danger"
              onClick={() => setConfirmMerge(true)}
              disabled={noteTooShort || resolve.isPending}
            >
              {t.clinic.reviewSamePerson}
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          <Alert tone="info">{t.clinic.reviewNothingMergedYet}</Alert>

          <ComparisonTable
            rows={rows}
            left={review.patient}
            right={review.candidate}
            survivorId={review.survivor_id}
          />

          <p className="text-sm text-[var(--text-secondary)]">
            {differences === 0
              ? t.clinic.reviewNoContradiction
              : t.clinic.reviewContradiction.replace(
                  '{n}',
                  String(differences),
                )}
          </p>

          <dl className="grid gap-x-6 gap-y-1 text-sm sm:grid-cols-[auto_1fr]">
            <dt className="text-[var(--text-secondary)]">
              {t.clinic.reviewRaisedBecause}
            </dt>
            <dd>{review.reason}</dd>
            <dt className="text-[var(--text-secondary)]">
              {t.clinic.reviewRaisedOn}
            </dt>
            <dd>{formatDate(review.created_at)}</dd>
          </dl>

          <Field
            label={t.clinic.reviewNote}
            hint={t.clinic.reviewNoteHint}
            required
            htmlFor="duplicate-review-note"
          >
            <Textarea
              id="duplicate-review-note"
              rows={2}
              value={note}
              onChange={(event) => setNote(event.target.value)}
              placeholder={t.clinic.reviewNotePlaceholder}
            />
          </Field>

          {error ? <Alert tone="danger">{error}</Alert> : null}
        </div>
      </Dialog>

      <Dialog
        open={confirmMerge}
        onClose={() => setConfirmMerge(false)}
        title={t.clinic.reviewConfirmMergeTitle}
        closeLabel={t.common.close}
        footer={
          <>
            <Button
              onClick={() => setConfirmMerge(false)}
              disabled={resolve.isPending}
            >
              {t.common.cancel}
            </Button>
            <Button
              variant="danger"
              onClick={() => resolve.mutate(true)}
              loading={resolve.isPending}
            >
              {t.clinic.reviewConfirmMergeAction}
            </Button>
          </>
        }
      >
        <div className="space-y-3 text-sm">
          <p>{t.clinic.reviewConfirmMergeBody}</p>
          <dl className="grid gap-x-6 gap-y-1 sm:grid-cols-[auto_1fr]">
            <dt className="text-[var(--text-secondary)]">
              {t.clinic.reviewKeeps}
            </dt>
            <dd className="font-medium">
              {surviving.display_name} · {surviving.code}
            </dd>
            <dt className="text-[var(--text-secondary)]">
              {t.clinic.reviewFiles}
            </dt>
            <dd className="font-medium">
              {filed.display_name} · {filed.code}
            </dd>
          </dl>
          <Alert tone="info">{t.clinic.reviewMergeKeepsHistory}</Alert>
        </div>
      </Dialog>
    </>
  )
}

function ComparisonTable({
  rows,
  left,
  right,
  survivorId,
}: {
  rows: FieldComparison[]
  left: PatientSummary
  right: PatientSummary
  survivorId: string
}) {
  const { t, formatDate } = useI18n()
  const labels: Record<IdentityField, string> = {
    family_name: t.clinic.familyName,
    given_name: t.clinic.givenName,
    patronymic: t.clinic.patronymic,
    birth_year: t.clinic.birthYear,
    phone: t.clinic.phone,
    address: t.clinic.address,
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[32rem] border-collapse text-sm">
        <thead>
          <tr className="border-b border-[var(--border)] text-left">
            <th className="py-2 pr-3 font-medium text-[var(--text-secondary)]">
              {t.clinic.reviewField}
            </th>
            <RecordHeader patient={left} isSurvivor={left.id === survivorId} />
            <RecordHeader patient={right} isSurvivor={right.id === survivorId} />
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.field} className="border-b border-[var(--border)]">
              <td className="py-1.5 pr-3 text-[var(--text-secondary)]">
                {labels[row.field]}
              </td>
              <ValueCell row={row} side="left" />
              <ValueCell row={row} side="right" />
            </tr>
          ))}
          <tr>
            <td className="py-1.5 pr-3 text-[var(--text-secondary)]">
              {t.clinic.registeredOn}
            </td>
            <td className="py-1.5 pr-3">{formatDate(left.registered_on)}</td>
            <td className="py-1.5">{formatDate(right.registered_on)}</td>
          </tr>
        </tbody>
      </table>
    </div>
  )
}

function RecordHeader({
  patient,
  isSurvivor,
}: {
  patient: PatientSummary
  isSurvivor: boolean
}) {
  const { t } = useI18n()
  return (
    <th className="py-2 pr-3 font-medium">
      <span className="block">{patient.code}</span>
      {isSurvivor ? (
        <span className="text-xs font-normal text-[var(--text-secondary)]">
          {t.clinic.reviewWouldBeKept}
        </span>
      ) : null}
    </th>
  )
}

function ValueCell({
  row,
  side,
}: {
  row: FieldComparison
  side: 'left' | 'right'
}) {
  const { t } = useI18n()
  const value = side === 'left' ? row.left : row.right
  // Only a positive contradiction is marked. A blank on one side is not a
  // disagreement, and colouring it as one is how a screen talks somebody out
  // of a merge that was correct.
  const emphasis =
    row.agreement === 'different'
      ? 'font-medium text-[var(--danger)]'
      : ''
  return (
    <td className={`py-1.5 pr-3 ${emphasis}`}>
      {value ?? (
        <span className="text-[var(--text-secondary)]">{t.clinic.reviewBlank}</span>
      )}
    </td>
  )
}
