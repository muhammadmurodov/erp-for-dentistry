import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useI18n } from '@/i18n'
import { useAuth } from '@/features/auth/AuthProvider'
import { api } from '@/lib/api'
import { queryKeys, useOrganization } from '@/lib/queries'
import { describeError } from '@/lib/errorMessage'
import {
  Alert,
  Button,
  Card,
  CardHeader,
  Checkbox,
  Field,
  Textarea,
} from '@/components/ui'
import type { Organization } from '@/lib/types'

/**
 * "This clinic has too few people to separate duties."
 *
 * The owner's setting and nobody else's. Segregation of duties assumes there
 * is somebody else standing there to approve what you did, and in a clinic
 * run by two or three family members there is not — so the control used to
 * produce a till that could not be closed, and in the end a shared login,
 * which is what actually destroys an audit trail.
 *
 * Switching it on grants nobody a permission. It removes only the
 * requirement that an approver be a *different* person, and every act that
 * then goes through on one pair of hands is marked as self-approved in the
 * audit trail (ADR-041). The reason is mandatory in both directions, because
 * turning it off can leave the clinic unable to close its own drawer.
 */
export function DutySeparationCard() {
  const { t, formatDate } = useI18n()
  const { can } = useAuth()
  const queryClient = useQueryClient()
  const organization = useOrganization()
  const [reason, setReason] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)
  // `null` means "whatever the organization currently says", so the checkbox
  // follows the server until somebody actually touches it.
  const [intended, setIntended] = useState<boolean | null>(null)

  const current = organization.data
  const declared = current?.duties_cannot_be_separated ?? false
  const next = intended ?? declared
  const changed = next !== declared
  const reasonTooShort = reason.trim().length < 3

  const save = useMutation({
    mutationFn: () =>
      api.put<Organization>('/organization/duty-separation', {
        duties_cannot_be_separated: next,
        reason: reason.trim(),
      }),
    onSuccess: (updated) => {
      queryClient.setQueryData(queryKeys.organization, updated)
      void queryClient.invalidateQueries({ queryKey: queryKeys.roles })
      setReason('')
      setIntended(null)
      setError(null)
      setSaved(true)
    },
    onError: (caught) => {
      setSaved(false)
      setError(describeError(caught, t))
    },
  })

  // Held by the owner role alone; everybody else does not see the card.
  if (!can('organization.duties.declare')) return null

  return (
    <Card>
      <CardHeader
        title={t.organization.dutySeparationTitle}
        description={t.organization.dutySeparationDescription}
      />
      <div className="space-y-4 p-4 pt-0">
        <Checkbox
          label={t.organization.dutySeparationLabel}
          description={t.organization.dutySeparationHint}
          checked={next}
          onChange={(event) => {
            setIntended(event.target.checked)
            setSaved(false)
          }}
        />

        {declared && current?.duties_declared_at ? (
          <p className="text-xs text-[var(--text-secondary)]">
            {t.organization.dutySeparationDeclaredOn.replace(
              '{date}',
              formatDate(current.duties_declared_at),
            )}
          </p>
        ) : null}

        {next ? (
          <Alert tone="warning">{t.organization.dutySeparationEffect}</Alert>
        ) : null}

        {changed ? (
          <Field
            label={t.organization.dutySeparationReason}
            hint={t.organization.dutySeparationReasonHint}
            required
            htmlFor="duty-separation-reason"
          >
            <Textarea
              id="duty-separation-reason"
              rows={2}
              value={reason}
              onChange={(event) => setReason(event.target.value)}
              placeholder={t.organization.dutySeparationReasonPlaceholder}
            />
          </Field>
        ) : null}

        {error ? <Alert tone="danger">{error}</Alert> : null}
        {saved ? <Alert tone="success">{t.organization.dutySeparationSaved}</Alert> : null}

        <div className="flex gap-2">
          <Button
            variant="primary"
            onClick={() => save.mutate()}
            disabled={!changed || reasonTooShort || save.isPending}
            loading={save.isPending}
          >
            {t.common.save}
          </Button>
          {changed ? (
            <Button
              onClick={() => {
                setIntended(null)
                setReason('')
                setError(null)
              }}
              disabled={save.isPending}
            >
              {t.common.cancel}
            </Button>
          ) : null}
        </div>
      </div>
    </Card>
  )
}
