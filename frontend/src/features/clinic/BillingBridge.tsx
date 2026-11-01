import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useI18n } from '@/i18n'
import { useAuth } from '@/features/auth/AuthProvider'
import { api } from '@/lib/api'
import { useBillable } from '@/lib/queries'
import { describeError } from '@/lib/errorMessage'
import {
  Alert,
  Button,
  Card,
  CardHeader,
  Checkbox,
  LoadingState,
} from '@/components/ui'

/**
 * Turning finished work into a bill.
 *
 * The step that used to be missing between the dentist and the cashier: work
 * was recorded and completed, and then somebody had to build an invoice by
 * hand and hope the lines matched.
 *
 * Three things this screen will not do, each of them deliberate:
 *
 * **It bills completed work only.** `COMPLETED` is the only billable status,
 * and the list comes from the server's own `billable` query — completed, with
 * no invoice line yet. A half-finished canal is not revenue.
 *
 * **It cannot bill the same tooth twice.** An item carries the invoice line
 * it produced, so once billed it leaves this list. The list is never cached,
 * because a stale one would offer work that another screen has already
 * invoiced, and the clinic would ask the patient for the same money again.
 *
 * **It drafts, it does not issue.** Issuing is what recognises revenue and
 * keeps its own permission (ADR). The dentist who did the work does not
 * thereby decide the clinic has earned it — that happens on the sales screen,
 * where the tax treatment is chosen.
 */
export function BillingBridge({
  patientId,
  billableCount,
  onBilled,
}: {
  patientId: string
  /**
   * How many finished items are waiting, from the patient detail — a plain
   * count, which carries no clinical content and is therefore available to
   * the cashier, who may raise the bill but may not read a finding.
   */
  billableCount: number
  onBilled: () => void
}) {
  const { t } = useI18n()
  const { can } = useAuth()
  const queryClient = useQueryClient()

  // Reading the billable list is clinical; raising the bill is a sales act.
  // Somebody who may do neither sees nothing at all.
  const mayRead = can('clinic.clinical.read')
  const mayBill = can('sales.invoice.manage')
  const billable = useBillable(patientId, mayRead)

  const [chosen, setChosen] = useState<string[]>([])
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  const bill = useMutation({
    mutationFn: () =>
      api.post<{ number: string }>(`/clinic/patients/${patientId}/invoices`, {
        // An empty selection means "everything billable", which is what the
        // backend does with a null list — and what a cashier means when they
        // press the button without ticking anything.
        item_ids: chosen.length > 0 ? chosen : null,
        issue_date: null,
      }),
    onSuccess: (invoice) => {
      setError(null)
      setChosen([])
      setNotice(t.clinic.draftedNotice.replace('{number}', invoice.number))
      void queryClient.invalidateQueries({ queryKey: ['clinic'] })
      onBilled()
    },
    onError: (caught) => {
      setNotice(null)
      setError(describeError(caught, t))
    },
  })

  if (billable.isLoading) return <LoadingState label={t.common.loading} />

  const items = billable.data ?? []
  // Whoever may read the clinical record sees the work itemised and can bill
  // a selection; the cashier, who may not, sees how much is waiting and bills
  // all of it. Both go through the same endpoint, which checks the same
  // permission — this only decides how much detail is on the screen.
  const waiting = mayRead ? items.length : billableCount
  // Nothing to bill is the ordinary state of this card, and an empty card on
  // every patient would be clutter.
  if (waiting === 0 && !notice) return null

  return (
    <Card>
      <CardHeader
        title={t.clinic.billableHeading}
        description={t.clinic.billableHint}
        actions={
          mayBill && waiting > 0 ? (
            <Button
              size="sm"
              variant="primary"
              loading={bill.isPending}
              onClick={() => bill.mutate()}
            >
              {chosen.length > 0
                ? t.clinic.draftSelected.replace('{n}', String(chosen.length))
                : t.clinic.draftAll}
            </Button>
          ) : undefined
        }
      />
      <div className="space-y-2 p-4 pt-0">
        {notice ? <Alert tone="success">{notice}</Alert> : null}
        {!mayRead && waiting > 0 ? (
          <p className="text-sm text-[var(--text-secondary)]">
            {t.clinic.billableCountOnly.replace('{n}', String(waiting))}
          </p>
        ) : null}
        {items.map((item) => (
          <Checkbox
            key={item.id}
            label={
              <span className="flex flex-wrap items-center gap-2">
                <span>
                  {item.tooth_number
                    ? `${t.clinic.tooth} ${item.tooth_number}`
                    : t.clinic.noTooth}
                </span>
                {item.tooth_surfaces ? (
                  <span className="text-[var(--text-secondary)]">
                    {item.tooth_surfaces}
                  </span>
                ) : null}
                {item.completed_on ? (
                  <span className="text-xs text-[var(--text-secondary)]">
                    {item.completed_on}
                  </span>
                ) : null}
              </span>
            }
            checked={chosen.includes(item.id)}
            disabled={!mayBill}
            onChange={(event) =>
              setChosen((current) =>
                event.target.checked
                  ? [...current, item.id]
                  : current.filter((id) => id !== item.id),
              )
            }
          />
        ))}
        {!mayBill && waiting > 0 ? (
          <Alert tone="info">{t.clinic.billableNoPermission}</Alert>
        ) : null}
        {error ? <Alert tone="danger">{error}</Alert> : null}
      </div>
    </Card>
  )
}
