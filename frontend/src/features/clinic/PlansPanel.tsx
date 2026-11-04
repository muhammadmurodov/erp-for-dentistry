import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useI18n } from '@/i18n'
import { useAuth } from '@/features/auth/AuthProvider'
import { api } from '@/lib/api'
import { usePlan, usePlans } from '@/lib/queries'
import { describeError, errorRequestId } from '@/lib/errorMessage'
import { completionBlockedReason, isFinal, nextStatuses } from '@/lib/plans'
import { planProgress } from '@/lib/plans'
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
  Textarea,
  Th,
} from '@/components/ui'
import type { PlanStatus, TreatmentItem, TreatmentPlan } from '@/lib/types'

/**
 * Courses of treatment: work that runs across several visits.
 *
 * The clinic's paper register shows prosthetic work spanning days as a matter
 * of course — an impression on one day, a fitting a week later — and until
 * now the system could record each tooth but had nothing to hold them
 * together. The backend has had `treatment_plan` since migration 0007; what
 * was missing was a way to see one.
 *
 * **A plan carries no money.** No price, no total, no quotation. Billing
 * still happens from completed items through the patient's account, which is
 * where the permission to raise a bill lives. A plan that priced itself would
 * be a second, informal invoice, and the one thing this system refuses to
 * have is two places where an amount is decided.
 */
export function PlansPanel({ patientId }: { patientId: string }) {
  const { t } = useI18n()
  const { can } = useAuth()
  const mayRead = can('clinic.clinical.read')
  const mayWrite = can('clinic.clinical.write')

  const [openPlanId, setOpenPlanId] = useState<string | null>(null)
  const [creating, setCreating] = useState(false)
  const plans = usePlans(patientId, mayRead)

  // Same rule as the clinical record: reception and the cashier see nothing
  // here rather than an access warning on every patient they open.
  if (!mayRead) return null

  if (openPlanId) {
    return (
      <PlanDetail
        planId={openPlanId}
        mayWrite={mayWrite}
        onBack={() => setOpenPlanId(null)}
      />
    )
  }

  if (plans.isLoading) return <LoadingState label={t.common.loading} />
  if (plans.isError) {
    return (
      <ErrorState
        title={t.errors.loadFailedTitle}
        message={describeError(plans.error, t)}
        requestId={errorRequestId(plans.error)}
        requestIdLabel={t.common.requestId}
        onRetry={() => void plans.refetch()}
        retryLabel={t.common.retry}
      />
    )
  }

  const rows = plans.data ?? []

  return (
    <>
      <Card>
        <CardHeader
          title={t.plans.heading}
          description={t.plans.hint}
          actions={
            mayWrite ? (
              <Button size="sm" variant="primary" onClick={() => setCreating(true)}>
                {t.plans.newPlan}
              </Button>
            ) : undefined
          }
        />
        {rows.length === 0 ? (
          <EmptyState title={t.plans.none} />
        ) : (
          <Table>
            <thead>
              <tr>
                <Th>{t.plans.code}</Th>
                <Th>{t.plans.title}</Th>
                <Th>{t.plans.status}</Th>
                <Th>{t.plans.started}</Th>
                <Th>{t.common.actions}</Th>
              </tr>
            </thead>
            <tbody>
              {rows.map((plan) => (
                <PlanRow key={plan.id} plan={plan} onOpen={() => setOpenPlanId(plan.id)} />
              ))}
            </tbody>
          </Table>
        )}
      </Card>

      {creating ? (
        <NewPlanDialog
          patientId={patientId}
          onClose={() => setCreating(false)}
          onCreated={(plan) => {
            setCreating(false)
            setOpenPlanId(plan.id)
          }}
        />
      ) : null}
    </>
  )
}

function PlanRow({ plan, onOpen }: { plan: TreatmentPlan; onOpen: () => void }) {
  const { t, formatDate } = useI18n()
  return (
    <tr>
      <Td>{plan.code}</Td>
      <Td>{plan.title}</Td>
      <Td>
        <PlanBadge status={plan.status} />
      </Td>
      <Td>{plan.started_on ? formatDate(plan.started_on) : '—'}</Td>
      <Td>
        <div className="flex justify-end">
          <Button size="sm" onClick={onOpen}>
            {t.plans.open}
          </Button>
        </div>
      </Td>
    </tr>
  )
}

// ------------------------------------------------------------------ one plan

function PlanDetail({
  planId,
  mayWrite,
  onBack,
}: {
  planId: string
  mayWrite: boolean
  onBack: () => void
}) {
  const { t, formatDate } = useI18n()
  const queryClient = useQueryClient()
  const plan = usePlan(planId, true)
  const [error, setError] = useState<string | null>(null)

  const move = useMutation({
    mutationFn: (status: PlanStatus) =>
      api.post(`/clinic/plans/${planId}/status`, { status }),
    onSuccess: () => {
      setError(null)
      void queryClient.invalidateQueries({ queryKey: ['clinic'] })
    },
    onError: (caught) => setError(describeError(caught, t)),
  })

  if (plan.isLoading) return <LoadingState label={t.common.loading} />
  if (plan.isError || !plan.data) {
    return (
      <ErrorState
        title={t.errors.loadFailedTitle}
        message={describeError(plan.error, t)}
        requestId={errorRequestId(plan.error)}
        requestIdLabel={t.common.requestId}
        onRetry={() => void plan.refetch()}
        retryLabel={t.common.retry}
      />
    )
  }

  const detail = plan.data
  const progress = planProgress(detail.items)
  const blocked = completionBlockedReason(detail.status, detail.open_item_count)

  return (
    <div className="space-y-4">
      <Button size="sm" onClick={onBack}>
        ← {t.plans.back}
      </Button>

      <Card>
        <CardHeader
          title={detail.title}
          description={`${t.plans.code}: ${detail.code}`}
          actions={<PlanBadge status={detail.status} />}
        />
        <div className="space-y-3 px-4 pb-4">
          <dl className="grid gap-x-6 gap-y-1 text-sm sm:grid-cols-2">
            <div className="flex justify-between gap-4 sm:block">
              <dt className="text-[var(--text-secondary)]">{t.plans.started}</dt>
              <dd>{detail.started_on ? formatDate(detail.started_on) : '—'}</dd>
            </div>
            <div className="flex justify-between gap-4 sm:block">
              <dt className="text-[var(--text-secondary)]">{t.plans.completed}</dt>
              <dd>{detail.completed_on ? formatDate(detail.completed_on) : '—'}</dd>
            </div>
          </dl>

          {progress.total > 0 ? (
            <p className="text-sm text-[var(--text-secondary)]">
              {t.plans.progress
                .replace('{done}', String(progress.done))
                .replace('{total}', String(progress.total))}
            </p>
          ) : null}

          {detail.notes ? (
            <p className="text-sm text-[var(--text-secondary)]">{detail.notes}</p>
          ) : null}

          {/*
            The reason a finished plan offers no buttons, said once, rather
            than a row of controls that all refuse.
          */}
          {isFinal(detail.status) ? (
            <Alert tone="info">{t.plans.final}</Alert>
          ) : blocked === 'workOutstanding' ? (
            <Alert tone="warning">{t.plans.completionBlockedWork}</Alert>
          ) : null}

          {mayWrite && !isFinal(detail.status) ? (
            <div className="flex flex-wrap gap-2">
              {nextStatuses(detail.status).map((status) => {
                const refuse = status === 'COMPLETED' && detail.open_item_count > 0
                return (
                  <Button
                    key={status}
                    size="sm"
                    variant={status === 'ABANDONED' ? 'ghost' : 'primary'}
                    disabled={refuse || move.isPending}
                    loading={move.isPending && move.variables === status}
                    onClick={() => move.mutate(status)}
                  >
                    {t.plans[`to${status}` as const]}
                  </Button>
                )
              })}
            </div>
          ) : null}

          {error ? <Alert tone="danger">{error}</Alert> : null}
        </div>
      </Card>

      <Card>
        <CardHeader title={t.plans.itemsHeading} />
        {detail.items.length === 0 ? (
          <EmptyState title={t.plans.noItems} />
        ) : (
          <Table>
            <thead>
              <tr>
                <Th>{t.clinic.tooth}</Th>
                <Th>{t.clinic.surfaces}</Th>
                <Th>{t.clinic.finding}</Th>
                <Th>{t.clinic.itemStatus}</Th>
                <Th>{t.clinic.billed}</Th>
              </tr>
            </thead>
            <tbody>
              {detail.items.map((item: TreatmentItem) => (
                <tr key={item.id}>
                  <Td>{item.tooth_number ?? '—'}</Td>
                  <Td>{item.tooth_surfaces ?? '—'}</Td>
                  <Td>{item.finding ?? '—'}</Td>
                  <Td>
                    <ItemStatusBadge status={item.status} />
                  </Td>
                  <Td>
                    {item.invoice_line_id ? (
                      <Badge tone="success">{t.clinic.billedYes}</Badge>
                    ) : item.status === 'COMPLETED' ? (
                      <Badge tone="warning">{t.clinic.billedNot}</Badge>
                    ) : (
                      '—'
                    )}
                  </Td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </Card>
    </div>
  )
}

// -------------------------------------------------------------------- badges

function PlanBadge({ status }: { status: PlanStatus }) {
  const { t } = useI18n()
  const map: Record<PlanStatus, { tone: 'neutral' | 'success' | 'warning' | 'brand'; label: string }> =
    {
      DRAFT: { tone: 'neutral', label: t.plans.statusDraft },
      PROPOSED: { tone: 'brand', label: t.plans.statusProposed },
      ACCEPTED: { tone: 'brand', label: t.plans.statusAccepted },
      IN_PROGRESS: { tone: 'warning', label: t.plans.statusInProgress },
      COMPLETED: { tone: 'success', label: t.plans.statusCompleted },
      ABANDONED: { tone: 'neutral', label: t.plans.statusAbandoned },
    }
  const { tone, label } = map[status]
  return <Badge tone={tone}>{label}</Badge>
}

function ItemStatusBadge({ status }: { status: TreatmentItem['status'] }) {
  const { t } = useI18n()
  const map: Record<
    TreatmentItem['status'],
    { tone: 'neutral' | 'success' | 'warning'; label: string }
  > = {
    PLANNED: { tone: 'neutral', label: t.clinic.itemPlanned },
    IN_PROGRESS: { tone: 'warning', label: t.clinic.itemInProgress },
    COMPLETED: { tone: 'success', label: t.clinic.itemCompleted },
    CANCELLED: { tone: 'neutral', label: t.clinic.itemCancelled },
    CORRECTED: { tone: 'neutral', label: t.clinic.itemCorrected },
  }
  const { tone, label } = map[status]
  return <Badge tone={tone}>{label}</Badge>
}

// -------------------------------------------------------------------- dialog

function NewPlanDialog({
  patientId,
  onClose,
  onCreated,
}: {
  patientId: string
  onClose: () => void
  onCreated: (plan: TreatmentPlan) => void
}) {
  const { t } = useI18n()
  const queryClient = useQueryClient()
  const [title, setTitle] = useState('')
  const [notes, setNotes] = useState('')
  const [error, setError] = useState<string | null>(null)

  const create = useMutation({
    mutationFn: () =>
      api.post<TreatmentPlan>('/clinic/plans', {
        patient_id: patientId,
        title: title.trim(),
        notes: notes.trim() === '' ? null : notes.trim(),
      }),
    onSuccess: (plan) => {
      void queryClient.invalidateQueries({ queryKey: ['clinic'] })
      onCreated(plan)
    },
    onError: (caught) => setError(describeError(caught, t)),
  })

  return (
    <Dialog
      open
      onClose={onClose}
      title={t.plans.newPlan}
      closeLabel={t.common.close}
      footer={
        <>
          <Button onClick={onClose} disabled={create.isPending}>
            {t.common.cancel}
          </Button>
          <Button
            variant="primary"
            disabled={title.trim().length === 0 || create.isPending}
            loading={create.isPending}
            onClick={() => create.mutate()}
          >
            {t.common.save}
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <p className="text-sm text-[var(--text-secondary)]">{t.plans.newPlanBody}</p>
        <Field label={t.plans.title} required htmlFor="plan-title">
          <Input
            id="plan-title"
            value={title}
            placeholder={t.plans.titlePlaceholder}
            onChange={(event) => setTitle(event.target.value)}
          />
        </Field>
        <Field label={t.plans.notes} htmlFor="plan-notes">
          <Textarea
            id="plan-notes"
            rows={2}
            value={notes}
            onChange={(event) => setNotes(event.target.value)}
          />
        </Field>
        {error ? <Alert tone="danger">{error}</Alert> : null}
      </div>
    </Dialog>
  )
}
