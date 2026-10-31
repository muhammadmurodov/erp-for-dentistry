import { useMemo, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useI18n } from '@/i18n'
import { useAuth } from '@/features/auth/AuthProvider'
import { api } from '@/lib/api'
import { useAccounts } from '@/lib/queries'
import { describeError } from '@/lib/errorMessage'
import {
  Alert,
  Button,
  Dialog,
  Field,
  Input,
  Select,
  Textarea,
} from '@/components/ui'
import type { JournalEntryDetail } from '@/lib/types'

interface DraftLine {
  account_code: string
  debit: string
  credit: string
  description: string
}

const EMPTY_LINE: DraftLine = {
  account_code: '',
  debit: '',
  credit: '',
  description: '',
}

/**
 * Adds decimal strings without going through `Number`.
 *
 * Amounts are strings all the way from the API to the screen precisely so
 * that no float ever touches them; summing them with `parseFloat` here would
 * reintroduce exactly the error the rest of the system avoids. This scales to
 * integer minor units, adds, and formats back.
 */
function addDecimalStrings(values: string[], scale = 2): string {
  let total = 0n
  for (const raw of values) {
    const value = raw.trim()
    if (!value) continue
    if (!/^-?\d+(\.\d+)?$/.test(value)) return 'NaN'
    const negative = value.startsWith('-')
    const [whole, fraction = ''] = (negative ? value.slice(1) : value).split('.')
    const padded = (fraction + '0'.repeat(scale)).slice(0, scale)
    const units = BigInt(whole + padded)
    total += negative ? -units : units
  }
  const negative = total < 0n
  const digits = (negative ? -total : total).toString().padStart(scale + 1, '0')
  const whole = digits.slice(0, -scale)
  const fraction = digits.slice(-scale)
  return `${negative ? '-' : ''}${whole}.${fraction}`
}

export function JournalEntryDialog({
  open,
  onClose,
  onSaved,
}: {
  open: boolean
  onClose: () => void
  onSaved: (message: string) => void
}) {
  const { t } = useI18n()
  const { can } = useAuth()
  const queryClient = useQueryClient()
  const accounts = useAccounts(open, { postable_only: true, active_only: true })

  const [entryDate, setEntryDate] = useState(
    () => new Date().toISOString().slice(0, 10),
  )
  const [description, setDescription] = useState('')
  const [reason, setReason] = useState('')
  const [lines, setLines] = useState<DraftLine[]>([
    { ...EMPTY_LINE },
    { ...EMPTY_LINE },
  ])
  const [formError, setFormError] = useState<string | null>(null)

  const mayPost = can('accounting.journal.post')

  const totals = useMemo(() => {
    const debit = addDecimalStrings(lines.map((l) => l.debit))
    const credit = addDecimalStrings(lines.map((l) => l.credit))
    const difference = addDecimalStrings([debit, `-${credit}`])
    return { debit, credit, difference, balanced: difference === '0.00' }
  }, [lines])

  const reset = () => {
    setDescription('')
    setReason('')
    setLines([{ ...EMPTY_LINE }, { ...EMPTY_LINE }])
    setFormError(null)
  }

  const save = useMutation({
    mutationFn: (postImmediately: boolean) =>
      api.post<JournalEntryDetail>('/accounting/journal', {
        entry_date: entryDate,
        description,
        reason,
        post_immediately: postImmediately,
        lines: lines
          .filter((line) => line.account_code && (line.debit || line.credit))
          .map((line) => ({
            account_code: line.account_code,
            // Sent as strings; the backend refuses JSON numbers outright.
            debit: line.debit || '0',
            credit: line.credit || '0',
            description: line.description || null,
          })),
      }),
    onSuccess: (entry) => {
      reset()
      void queryClient.invalidateQueries({ queryKey: ['accounting'] })
      onSaved(
        entry.status === 'POSTED'
          ? `${t.accounting.entryPosted} ${entry.entry_number}`
          : `${t.accounting.draftSaved} ${entry.entry_number}`,
      )
    },
    onError: (error) => setFormError(describeError(error, t)),
  })

  const submit = (postImmediately: boolean) => {
    setFormError(null)
    const filled = lines.filter(
      (line) => line.account_code && (line.debit || line.credit),
    )
    if (filled.length < 2) {
      setFormError(t.accounting.linesRequired)
      return
    }
    if (!totals.balanced) {
      setFormError(
        `${t.accounting.balanceError} ${t.accounting.difference}: ${totals.difference}`,
      )
      return
    }
    if (!reason.trim()) {
      setFormError(t.accounting.reasonHint)
      return
    }
    save.mutate(postImmediately)
  }

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={t.accounting.newEntryTitle}
      closeLabel={t.common.close}
      size="lg"
      footer={
        <>
          <Button onClick={onClose} disabled={save.isPending}>
            {t.common.cancel}
          </Button>
          <Button onClick={() => submit(false)} loading={save.isPending}>
            {t.accounting.saveDraft}
          </Button>
          {/* Posting needs its own permission, so the button is only offered
              to someone who actually holds it. */}
          {mayPost ? (
            <Button
              variant="primary"
              onClick={() => submit(true)}
              loading={save.isPending}
            >
              {t.accounting.postImmediately}
            </Button>
          ) : null}
        </>
      }
    >
      {formError ? (
        <div className="mb-4">
          <Alert tone="danger">{formError}</Alert>
        </div>
      ) : null}

      <div className="flex flex-col gap-4">
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label={t.accounting.entryDate} htmlFor="entry-date" required>
            <Input
              id="entry-date"
              type="date"
              value={entryDate}
              onChange={(event) => setEntryDate(event.target.value)}
            />
          </Field>
          <Field label={t.accounting.reason} htmlFor="entry-reason" required
                 hint={t.accounting.reasonHint}>
            <Input
              id="entry-reason"
              value={reason}
              onChange={(event) => setReason(event.target.value)}
            />
          </Field>
        </div>

        <Field label={t.accounting.description} htmlFor="entry-description" required>
          <Textarea
            id="entry-description"
            rows={2}
            value={description}
            onChange={(event) => setDescription(event.target.value)}
          />
        </Field>

        <div className="flex min-w-0 flex-col gap-2">
          <div className="grid grid-cols-[minmax(0,1fr)_8rem_8rem_2.5rem] gap-2 text-xs font-semibold text-[var(--text-secondary)]">
            <span>{t.accounting.account}</span>
            <span className="text-right">{t.accounting.debit}</span>
            <span className="text-right">{t.accounting.credit}</span>
            <span />
          </div>

          {lines.map((line, index) => (
            <div
              key={index}
              className="grid grid-cols-[minmax(0,1fr)_8rem_8rem_2.5rem] items-center gap-2"
            >
              <Select
                className="w-full min-w-0"
                aria-label={`${t.accounting.account} ${index + 1}`}
                value={line.account_code}
                onChange={(event) =>
                  setLines((current) =>
                    current.map((l, i) =>
                      i === index ? { ...l, account_code: event.target.value } : l,
                    ),
                  )
                }
              >
                <option value="">—</option>
                {(accounts.data ?? []).map((account) => (
                  <option key={account.id} value={account.code}>
                    {account.code} · {account.name}
                  </option>
                ))}
              </Select>
              <Input
                aria-label={`${t.accounting.debit} ${index + 1}`}
                inputMode="decimal"
                className="tabular w-full min-w-0 text-right"
                value={line.debit}
                onChange={(event) =>
                  setLines((current) =>
                    current.map((l, i) =>
                      // A line is a debit or a credit, never both.
                      i === index
                        ? { ...l, debit: event.target.value, credit: '' }
                        : l,
                    ),
                  )
                }
              />
              <Input
                aria-label={`${t.accounting.credit} ${index + 1}`}
                inputMode="decimal"
                className="tabular w-full min-w-0 text-right"
                value={line.credit}
                onChange={(event) =>
                  setLines((current) =>
                    current.map((l, i) =>
                      i === index
                        ? { ...l, credit: event.target.value, debit: '' }
                        : l,
                    ),
                  )
                }
              />
              <Button
                size="sm"
                variant="ghost"
                aria-label={t.accounting.removeLine}
                disabled={lines.length <= 2}
                onClick={() =>
                  setLines((current) => current.filter((_, i) => i !== index))
                }
              >
                ✕
              </Button>
            </div>
          ))}

          <div>
            <Button
              size="sm"
              onClick={() => setLines((current) => [...current, { ...EMPTY_LINE }])}
            >
              {t.accounting.addLine}
            </Button>
          </div>

          {/* Live balance check: the same rule the backend enforces, shown
              while typing so the refusal is never a surprise. */}
          <div className="grid grid-cols-[minmax(0,1fr)_8rem_8rem_2.5rem] gap-2 border-t border-[var(--border-subtle)] pt-2 text-sm">
            <span className="font-medium">{t.accounting.total}</span>
            <span className="tabular text-right font-medium">{totals.debit}</span>
            <span className="tabular text-right font-medium">{totals.credit}</span>
            <span />
          </div>
          {!totals.balanced ? (
            <p className="text-xs" style={{ color: 'var(--danger)' }} role="alert">
              {t.accounting.difference}: {totals.difference}
            </p>
          ) : null}
        </div>
      </div>
    </Dialog>
  )
}
