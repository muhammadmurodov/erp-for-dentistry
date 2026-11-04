import { useMemo, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useI18n } from '@/i18n'
import { useAuth } from '@/features/auth/AuthProvider'
import { api } from '@/lib/api'
import { useEncounters, usePlans, useServices, useTreatments } from '@/lib/queries'
import { describeError, errorRequestId } from '@/lib/errorMessage'
import { parseSurfaces, parseTooth } from '@/lib/fdi'
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
  Select,
  Table,
  Td,
  Textarea,
  Th,
} from '@/components/ui'
import type { Encounter, TreatmentItem, TreatmentItemStatus } from '@/lib/types'

/**
 * The clinical half of a patient's record: visits, and the work done on each
 * tooth.
 *
 * This is the screen that used to not exist — a dentist had to record a tooth
 * by calling the API directly. Everything behind it was already built and
 * tested; what was missing was somewhere to type.
 *
 * **Access follows the clinical permission, not the patient one.** Reception
 * and the cashier may open a patient and take money without ever seeing a
 * finding: this whole panel renders nothing for them. That is the provisional
 * policy while `OPEN_QUESTIONS.md` C1 is unanswered, and it is the strict
 * reading — if the clinic later decides reception may see a treatment name
 * without the finding, that loosens, which is the safe direction to move in.
 *
 * **A visit does not imply a bill.** Recording work does not price it and
 * does not invoice it; completing an item is what makes it billable, and
 * raising the bill is a separate act with its own permission. The clinic's
 * own register is full of attendances that produced no revenue — a check of
 * earlier work, a patient sent away — and the screen has to allow for that.
 */
export function ClinicalPanel({ patientId }: { patientId: string }) {
  const { t } = useI18n()
  const { can } = useAuth()

  const mayRead = can('clinic.clinical.read')
  const mayWrite = can('clinic.clinical.write')
  const mayComplete = can('clinic.treatment.complete')

  const encounters = useEncounters(patientId, mayRead)
  const treatments = useTreatments(patientId, mayRead)
  const [openEncounter, setOpenEncounter] = useState(false)
  const [recordingInto, setRecordingInto] = useState<Encounter | null>(null)

  // Not an error state: a cashier opening a patient simply has no clinical
  // record on their screen, and saying so would be noise on every visit.
  if (!mayRead) return null

  if (encounters.isLoading || treatments.isLoading) {
    return <LoadingState label={t.common.loading} />
  }
  if (encounters.isError) {
    return (
      <ErrorState
        title={t.errors.loadFailedTitle}
        message={describeError(encounters.error, t)}
        requestId={errorRequestId(encounters.error)}
        requestIdLabel={t.common.requestId}
        onRetry={() => void encounters.refetch()}
        retryLabel={t.common.retry}
      />
    )
  }

  const visits = encounters.data ?? []
  const items = treatments.data ?? []
  const current = visits.find((v) => v.status === 'OPEN') ?? null

  return (
    <>
      <Card>
        <CardHeader
          title={t.clinic.clinicalHeading}
          description={t.clinic.clinicalHint}
          actions={
            mayWrite ? (
              current ? (
                <Button
                  size="sm"
                  variant="primary"
                  onClick={() => setRecordingInto(current)}
                >
                  {t.clinic.recordTreatment}
                </Button>
              ) : (
                <Button size="sm" variant="primary" onClick={() => setOpenEncounter(true)}>
                  {t.clinic.openEncounter}
                </Button>
              )
            ) : undefined
          }
        />

        {current ? (
          <div className="px-4 pb-3">
            <Alert tone="info">
              {t.clinic.encounterOpenNotice.replace('{code}', current.code)}
            </Alert>
          </div>
        ) : null}

        {visits.length === 0 ? (
          <EmptyState title={t.clinic.noEncounters} />
        ) : (
          <ul className="divide-y divide-[var(--border-subtle)]">
            {visits.map((visit) => (
              <EncounterRow
                key={visit.id}
                encounter={visit}
                items={items.filter((i) => i.encounter_id === visit.id)}
                mayWrite={mayWrite}
                mayComplete={mayComplete}
                onRecord={() => setRecordingInto(visit)}
              />
            ))}
          </ul>
        )}
      </Card>

      {openEncounter ? (
        <OpenEncounterDialog
          patientId={patientId}
          onClose={() => setOpenEncounter(false)}
          onOpened={(encounter) => {
            setOpenEncounter(false)
            setRecordingInto(encounter)
          }}
        />
      ) : null}

      {recordingInto ? (
        <RecordItemsDialog
          encounter={recordingInto}
          onClose={() => setRecordingInto(null)}
        />
      ) : null}
    </>
  )
}

// ------------------------------------------------------------------- a visit

function EncounterRow({
  encounter,
  items,
  mayWrite,
  mayComplete,
  onRecord,
}: {
  encounter: Encounter
  items: TreatmentItem[]
  mayWrite: boolean
  mayComplete: boolean
  onRecord: () => void
}) {
  const { t, formatDate } = useI18n()
  const queryClient = useQueryClient()
  const [error, setError] = useState<string | null>(null)
  const [notes, setNotes] = useState('')
  const [completing, setCompleting] = useState(false)

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ['clinic'] })
  }

  const completeEncounter = useMutation({
    mutationFn: () =>
      api.post(`/clinic/encounters/${encounter.id}/complete`, {
        clinical_notes: notes.trim() === '' ? null : notes.trim(),
      }),
    onSuccess: () => {
      setCompleting(false)
      setNotes('')
      setError(null)
      invalidate()
    },
    onError: (caught) => setError(describeError(caught, t)),
  })

  return (
    <li className="px-4 py-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="flex flex-wrap items-center gap-2 text-sm font-medium">
            {formatDate(encounter.occurred_on)}
            <span className="text-[var(--text-secondary)]">{encounter.code}</span>
            <EncounterBadge status={encounter.status} />
          </p>
          {encounter.complaint ? (
            <p className="mt-0.5 text-sm text-[var(--text-secondary)]">
              {t.clinic.complaint}: {encounter.complaint}
            </p>
          ) : null}
          {encounter.clinical_notes ? (
            <p className="mt-0.5 text-sm text-[var(--text-secondary)]">
              {encounter.clinical_notes}
            </p>
          ) : null}
        </div>
        {encounter.status === 'OPEN' && mayWrite ? (
          <div className="flex shrink-0 gap-2">
            <Button size="sm" onClick={onRecord}>
              {t.clinic.recordTreatment}
            </Button>
            <Button size="sm" onClick={() => setCompleting(true)}>
              {t.clinic.completeEncounter}
            </Button>
          </div>
        ) : null}
      </div>

      {items.length > 0 ? (
        <div className="mt-2">
          <TreatmentTable items={items} mayComplete={mayComplete} mayWrite={mayWrite} />
        </div>
      ) : encounter.status === 'OPEN' ? (
        <p className="mt-2 text-sm text-[var(--text-secondary)]">
          {t.clinic.noItemsYet}
        </p>
      ) : null}

      {error ? (
        <div className="mt-2">
          <Alert tone="danger">{error}</Alert>
        </div>
      ) : null}

      <Dialog
        open={completing}
        onClose={() => setCompleting(false)}
        title={t.clinic.completeEncounter}
        closeLabel={t.common.close}
        footer={
          <>
            <Button onClick={() => setCompleting(false)} disabled={completeEncounter.isPending}>
              {t.common.cancel}
            </Button>
            <Button
              variant="primary"
              onClick={() => completeEncounter.mutate()}
              loading={completeEncounter.isPending}
            >
              {t.clinic.completeEncounter}
            </Button>
          </>
        }
      >
        <div className="space-y-3">
          <p className="text-sm text-[var(--text-secondary)]">
            {t.clinic.completeEncounterBody}
          </p>
          <Field label={t.clinic.clinicalNotes} htmlFor="encounter-notes">
            <Textarea
              id="encounter-notes"
              rows={3}
              value={notes}
              onChange={(event) => setNotes(event.target.value)}
            />
          </Field>
        </div>
      </Dialog>
    </li>
  )
}

function EncounterBadge({ status }: { status: Encounter['status'] }) {
  const { t } = useI18n()
  if (status === 'OPEN') return <Badge tone="warning">{t.clinic.encounterOpen}</Badge>
  if (status === 'COMPLETED')
    return <Badge tone="success">{t.clinic.encounterCompleted}</Badge>
  return <Badge tone="neutral">{t.clinic.encounterCancelled}</Badge>
}

// ------------------------------------------------------- the work, per tooth

function TreatmentTable({
  items,
  mayComplete,
  mayWrite,
}: {
  items: TreatmentItem[]
  mayComplete: boolean
  mayWrite: boolean
}) {
  const { t } = useI18n()
  const queryClient = useQueryClient()
  const [error, setError] = useState<string | null>(null)
  const [cancelling, setCancelling] = useState<TreatmentItem | null>(null)
  const [correcting, setCorrecting] = useState<TreatmentItem | null>(null)
  const [reason, setReason] = useState('')

  const invalidate = () => void queryClient.invalidateQueries({ queryKey: ['clinic'] })

  const complete = useMutation({
    mutationFn: (itemId: string) =>
      api.post(`/clinic/treatments/${itemId}/complete`, { completed_on: null }),
    onSuccess: () => {
      setError(null)
      invalidate()
    },
    onError: (caught) => setError(describeError(caught, t)),
  })

  const cancel = useMutation({
    mutationFn: (itemId: string) =>
      api.post(`/clinic/treatments/${itemId}/cancel`, { reason: reason.trim() }),
    onSuccess: () => {
      setCancelling(null)
      setReason('')
      setError(null)
      invalidate()
    },
    onError: (caught) => setError(describeError(caught, t)),
  })

  return (
    <>
      <Table>
        <thead>
          <tr>
            <Th>{t.clinic.tooth}</Th>
            <Th>{t.clinic.surfaces}</Th>
            <Th>{t.clinic.finding}</Th>
            <Th>{t.clinic.itemStatus}</Th>
            <Th>{t.clinic.billed}</Th>
            {mayComplete || mayWrite ? <Th>{t.common.actions}</Th> : null}
          </tr>
        </thead>
        <tbody>
          {items.map((item) => (
            <tr key={item.id}>
              <Td>{item.tooth_number ?? '—'}</Td>
              <Td>{item.tooth_surfaces ?? '—'}</Td>
              <Td>{item.finding ?? '—'}</Td>
              <Td>
                <ItemBadge status={item.status} />
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
              {mayComplete || mayWrite ? (
                <Td>
                  <div className="flex justify-end gap-1">
                    {mayComplete &&
                    (item.status === 'PLANNED' || item.status === 'IN_PROGRESS') ? (
                      <Button
                        size="sm"
                        onClick={() => complete.mutate(item.id)}
                        loading={complete.isPending && complete.variables === item.id}
                      >
                        {t.clinic.markDone}
                      </Button>
                    ) : null}
                    {mayWrite &&
                    (item.status === 'PLANNED' || item.status === 'IN_PROGRESS') ? (
                      <Button size="sm" variant="ghost" onClick={() => setCancelling(item)}>
                        {t.common.cancel}
                      </Button>
                    ) : null}
                    {/*
                      Correction is offered for a completed item too — that is
                      the case it exists for, the tooth written down wrong and
                      noticed afterwards. It stops at the bill: once an item
                      has reached an invoice the money has moved, and the
                      invoice is corrected first. The server refuses either
                      way; the button simply does not appear.
                    */}
                    {mayWrite &&
                    item.status !== 'CORRECTED' &&
                    item.status !== 'CANCELLED' &&
                    item.invoice_line_id === null ? (
                      <Button size="sm" variant="ghost" onClick={() => setCorrecting(item)}>
                        {t.clinic.correctItem}
                      </Button>
                    ) : null}
                  </div>
                </Td>
              ) : null}
            </tr>
          ))}
        </tbody>
      </Table>
      {error ? <Alert tone="danger">{error}</Alert> : null}

      <Dialog
        open={cancelling !== null}
        onClose={() => setCancelling(null)}
        title={t.clinic.cancelItem}
        closeLabel={t.common.close}
        footer={
          <>
            <Button onClick={() => setCancelling(null)} disabled={cancel.isPending}>
              {t.common.close}
            </Button>
            <Button
              variant="danger"
              disabled={reason.trim().length < 3 || cancel.isPending}
              loading={cancel.isPending}
              onClick={() => cancelling && cancel.mutate(cancelling.id)}
            >
              {t.clinic.cancelItem}
            </Button>
          </>
        }
      >
        <div className="space-y-3">
          <p className="text-sm text-[var(--text-secondary)]">{t.clinic.cancelItemBody}</p>
          <Field label={t.clinic.reason} required htmlFor="cancel-item-reason">
            <Textarea
              id="cancel-item-reason"
              rows={2}
              value={reason}
              onChange={(event) => setReason(event.target.value)}
            />
          </Field>
        </div>
      </Dialog>

      {correcting ? (
        <CorrectItemDialog item={correcting} onClose={() => setCorrecting(null)} />
      ) : null}
    </>
  )
}

/**
 * Supersede a recorded treatment with what really happened.
 *
 * Not an edit. The original stays on the record, marked `CORRECTED` and
 * pointing at its replacement, and the reason is kept with it — a clinical
 * record that can be changed in place is not a clinical record. This screen
 * is the reason the API endpoint stopped being the only way to reach that:
 * the dentist who notices tooth 26 written where 36 was treated is not going
 * to open a terminal.
 */
function CorrectItemDialog({
  item,
  onClose,
}: {
  item: TreatmentItem
  onClose: () => void
}) {
  const { t } = useI18n()
  const queryClient = useQueryClient()
  const services = useServices(true)
  const [serviceId, setServiceId] = useState(item.service_id)
  const [tooth, setTooth] = useState(
    item.tooth_number === null ? '' : String(item.tooth_number),
  )
  const [surfaces, setSurfaces] = useState(item.tooth_surfaces ?? '')
  const [finding, setFinding] = useState(item.finding ?? '')
  const [reason, setReason] = useState('')
  const [error, setError] = useState<string | null>(null)

  const parsedTooth = parseTooth(tooth)
  const parsedSurfaces = parseSurfaces(surfaces)
  const canSubmit =
    serviceId !== '' &&
    reason.trim().length >= 3 &&
    !parsedTooth.error &&
    !parsedSurfaces.error

  const correct = useMutation({
    mutationFn: () =>
      api.post(`/clinic/treatments/${item.id}/correct`, {
        reason: reason.trim(),
        replacement: {
          service_id: serviceId,
          tooth_number: parsedTooth.tooth,
          tooth_surfaces: parsedSurfaces.surfaces,
          finding: finding.trim() === '' ? null : finding.trim(),
          quantity: item.quantity,
          // The replacement keeps the state the original had reached. A
          // correction fixes what was recorded, it does not finish the work
          // or un-finish it.
          status: item.status,
        },
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['clinic'] })
      onClose()
    },
    onError: (caught) => setError(describeError(caught, t)),
  })

  return (
    <Dialog
      open
      size="lg"
      onClose={onClose}
      title={t.clinic.correctItem}
      closeLabel={t.common.close}
      footer={
        <>
          <Button onClick={onClose} disabled={correct.isPending}>
            {t.common.cancel}
          </Button>
          <Button
            variant="primary"
            disabled={!canSubmit || correct.isPending}
            loading={correct.isPending}
            onClick={() => correct.mutate()}
          >
            {t.clinic.correctItem}
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <Alert tone="info">{t.clinic.correctItemBody}</Alert>

        <Field label={t.clinic.service} htmlFor="correct-service">
          <Select
            id="correct-service"
            value={serviceId}
            onChange={(event) => setServiceId(event.target.value)}
          >
            {(services.data ?? []).map((service) => (
              <option key={service.id} value={service.id}>
                {service.name}
              </option>
            ))}
          </Select>
        </Field>

        <div className="grid gap-3 sm:grid-cols-2">
          <Field
            label={t.clinic.tooth}
            htmlFor="correct-tooth"
            hint={t.clinic.toothHint}
            error={
              parsedTooth.error
                ? parsedTooth.error === 'toothNotANumber'
                  ? t.clinic.toothNotANumber
                  : t.clinic.toothOutOfRange
                : undefined
            }
          >
            <Input
              id="correct-tooth"
              inputMode="numeric"
              value={tooth}
              onChange={(event) => setTooth(event.target.value)}
            />
          </Field>
          <Field
            label={t.clinic.surfaces}
            htmlFor="correct-surfaces"
            error={parsedSurfaces.error ? t.clinic.surfacesInvalid : undefined}
          >
            <Input
              id="correct-surfaces"
              value={surfaces}
              onChange={(event) => setSurfaces(event.target.value)}
            />
          </Field>
        </div>

        <Field label={t.clinic.finding} htmlFor="correct-finding">
          <Input
            id="correct-finding"
            value={finding}
            onChange={(event) => setFinding(event.target.value)}
          />
        </Field>

        <Field label={t.clinic.reason} required htmlFor="correct-reason">
          <Textarea
            id="correct-reason"
            rows={2}
            value={reason}
            onChange={(event) => setReason(event.target.value)}
            placeholder={t.clinic.correctItemReasonPlaceholder}
          />
        </Field>

        {error ? <Alert tone="danger">{error}</Alert> : null}
      </div>
    </Dialog>
  )
}

function ItemBadge({ status }: { status: TreatmentItemStatus }) {
  const { t } = useI18n()
  const map: Record<TreatmentItemStatus, { tone: 'neutral' | 'success' | 'warning'; label: string }> =
    {
      PLANNED: { tone: 'neutral', label: t.clinic.itemPlanned },
      IN_PROGRESS: { tone: 'warning', label: t.clinic.itemInProgress },
      COMPLETED: { tone: 'success', label: t.clinic.itemCompleted },
      CANCELLED: { tone: 'neutral', label: t.clinic.itemCancelled },
      CORRECTED: { tone: 'neutral', label: t.clinic.itemCorrected },
    }
  const { tone, label } = map[status]
  return <Badge tone={tone}>{label}</Badge>
}

// ------------------------------------------------------------------ dialogs

function OpenEncounterDialog({
  patientId,
  onClose,
  onOpened,
}: {
  patientId: string
  onClose: () => void
  onOpened: (encounter: Encounter) => void
}) {
  const { t } = useI18n()
  const queryClient = useQueryClient()
  const [complaint, setComplaint] = useState('')
  const [error, setError] = useState<string | null>(null)

  const open = useMutation({
    mutationFn: () =>
      api.post<Encounter>('/clinic/encounters', {
        patient_id: patientId,
        occurred_on: null,
        complaint: complaint.trim() === '' ? null : complaint.trim(),
      }),
    onSuccess: (encounter) => {
      void queryClient.invalidateQueries({ queryKey: ['clinic'] })
      onOpened(encounter)
    },
    onError: (caught) => setError(describeError(caught, t)),
  })

  return (
    <Dialog
      open
      onClose={onClose}
      title={t.clinic.openEncounter}
      closeLabel={t.common.close}
      footer={
        <>
          <Button onClick={onClose} disabled={open.isPending}>
            {t.common.cancel}
          </Button>
          <Button variant="primary" onClick={() => open.mutate()} loading={open.isPending}>
            {t.clinic.openEncounter}
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <p className="text-sm text-[var(--text-secondary)]">{t.clinic.openEncounterBody}</p>
        <Field label={t.clinic.complaint} htmlFor="encounter-complaint">
          <Textarea
            id="encounter-complaint"
            rows={2}
            value={complaint}
            onChange={(event) => setComplaint(event.target.value)}
            placeholder={t.clinic.complaintPlaceholder}
          />
        </Field>
        {error ? <Alert tone="danger">{error}</Alert> : null}
      </div>
    </Dialog>
  )
}

interface DraftItem {
  key: number
  serviceId: string
  tooth: string
  surfaces: string
  finding: string
}

function RecordItemsDialog({
  encounter,
  onClose,
}: {
  encounter: Encounter
  onClose: () => void
}) {
  const { t } = useI18n()
  const queryClient = useQueryClient()
  const services = useServices(true)
  // Only courses still open are offered: attaching a new tooth to a finished
  // or abandoned plan would reopen a decision somebody already made.
  const plans = usePlans(encounter.patient_id, true)
  const openPlans = (plans.data ?? []).filter(
    (plan) => plan.status !== 'COMPLETED' && plan.status !== 'ABANDONED',
  )
  const [planId, setPlanId] = useState('')
  const [rows, setRows] = useState<DraftItem[]>([
    { key: 1, serviceId: '', tooth: '', surfaces: '', finding: '' },
  ])
  const [error, setError] = useState<string | null>(null)

  const parsed = useMemo(
    () =>
      rows.map((row) => ({
        row,
        tooth: parseTooth(row.tooth),
        surfaces: parseSurfaces(row.surfaces),
      })),
    [rows],
  )
  const firstError = parsed.find((p) => p.tooth.error || p.surfaces.error)
  const usable = rows.filter((r) => r.serviceId !== '')
  const canSubmit = usable.length > 0 && !firstError

  const record = useMutation({
    mutationFn: () =>
      api.post<unknown>(`/clinic/encounters/${encounter.id}/items`, {
        plan_id: planId === '' ? null : planId,
        items: parsed
          .filter((p) => p.row.serviceId !== '')
          .map((p) => ({
            service_id: p.row.serviceId,
            tooth_number: p.tooth.tooth,
            tooth_surfaces: p.surfaces.surfaces,
            finding: p.row.finding.trim() === '' ? null : p.row.finding.trim(),
            quantity: '1',
            status: 'IN_PROGRESS',
          })),
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['clinic'] })
      onClose()
    },
    onError: (caught) => setError(describeError(caught, t)),
  })

  return (
    <Dialog
      open
      size="lg"
      onClose={onClose}
      title={t.clinic.recordTreatment}
      closeLabel={t.common.close}
      footer={
        <>
          <Button onClick={onClose} disabled={record.isPending}>
            {t.common.cancel}
          </Button>
          <Button
            variant="primary"
            disabled={!canSubmit || record.isPending}
            loading={record.isPending}
            onClick={() => record.mutate()}
          >
            {t.common.save}
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <p className="text-sm text-[var(--text-secondary)]">
          {t.clinic.recordTreatmentBody}
        </p>

        {openPlans.length > 0 ? (
          <Field label={t.plans.chooseForItems} htmlFor="record-plan">
            <Select
              id="record-plan"
              value={planId}
              onChange={(event) => setPlanId(event.target.value)}
            >
              <option value="">{t.plans.noPlan}</option>
              {openPlans.map((plan) => (
                <option key={plan.id} value={plan.id}>
                  {plan.code} · {plan.title}
                </option>
              ))}
            </Select>
          </Field>
        ) : null}

        <div className="space-y-3">
          {rows.map((row, index) => {
            const p = parsed[index]
            return (
              <div
                key={row.key}
                className="grid gap-2 rounded-lg border border-[var(--border-subtle)] p-3 sm:grid-cols-[1fr_6rem_6rem]"
              >
                <Field label={t.clinic.service} htmlFor={`item-service-${row.key}`}>
                  <Select
                    id={`item-service-${row.key}`}
                    value={row.serviceId}
                    onChange={(event) =>
                      setRows((current) =>
                        current.map((r) =>
                          r.key === row.key ? { ...r, serviceId: event.target.value } : r,
                        ),
                      )
                    }
                  >
                    <option value="">{t.clinic.choose}</option>
                    {(services.data ?? []).map((service) => (
                      <option key={service.id} value={service.id}>
                        {service.name}
                      </option>
                    ))}
                  </Select>
                </Field>
                <Field
                  label={t.clinic.tooth}
                  htmlFor={`item-tooth-${row.key}`}
                  hint={t.clinic.toothHint}
                  error={
                    p?.tooth.error
                      ? p.tooth.error === 'toothNotANumber'
                        ? t.clinic.toothNotANumber
                        : t.clinic.toothOutOfRange
                      : undefined
                  }
                >
                  <Input
                    id={`item-tooth-${row.key}`}
                    inputMode="numeric"
                    value={row.tooth}
                    onChange={(event) =>
                      setRows((current) =>
                        current.map((r) =>
                          r.key === row.key ? { ...r, tooth: event.target.value } : r,
                        ),
                      )
                    }
                  />
                </Field>
                <Field
                  label={t.clinic.surfaces}
                  htmlFor={`item-surfaces-${row.key}`}
                  error={p?.surfaces.error ? t.clinic.surfacesInvalid : undefined}
                >
                  <Input
                    id={`item-surfaces-${row.key}`}
                    value={row.surfaces}
                    onChange={(event) =>
                      setRows((current) =>
                        current.map((r) =>
                          r.key === row.key ? { ...r, surfaces: event.target.value } : r,
                        ),
                      )
                    }
                  />
                </Field>
                <div className="sm:col-span-3">
                  <Field label={t.clinic.finding} htmlFor={`item-finding-${row.key}`}>
                    <Input
                      id={`item-finding-${row.key}`}
                      value={row.finding}
                      onChange={(event) =>
                        setRows((current) =>
                          current.map((r) =>
                            r.key === row.key ? { ...r, finding: event.target.value } : r,
                          ),
                        )
                      }
                    />
                  </Field>
                </div>
              </div>
            )
          })}
        </div>

        <Button
          size="sm"
          onClick={() =>
            setRows((current) => [
              ...current,
              {
                key: Math.max(0, ...current.map((r) => r.key)) + 1,
                serviceId: '',
                tooth: '',
                surfaces: '',
                finding: '',
              },
            ])
          }
        >
          {t.clinic.addTooth}
        </Button>

        {error ? <Alert tone="danger">{error}</Alert> : null}
      </div>
    </Dialog>
  )
}
