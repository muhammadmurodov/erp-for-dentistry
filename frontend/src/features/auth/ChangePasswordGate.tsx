import { useState } from 'react'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'
import { useI18n } from '@/i18n'
import { useAuth } from './AuthProvider'
import { api } from '@/lib/api'
import { describeError } from '@/lib/errorMessage'
import { Alert, Button, Card, Field, Input } from '@/components/ui'

const schema = z
  .object({
    current_password: z.string().min(1),
    new_password: z.string().min(12),
    confirm_password: z.string().min(1),
  })
  .refine((values) => values.new_password === values.confirm_password, {
    path: ['confirm_password'],
    message: 'mismatch',
  })

type FormValues = z.infer<typeof schema>

/**
 * Blocks the app while the account is flagged `must_change_password`.
 *
 * A new user is created with a password somebody else chose; until they have
 * replaced it, that password is known to two people, so nothing else should
 * be reachable.
 */
export function ChangePasswordGate() {
  const { t } = useI18n()
  const { signOut, refreshUser } = useAuth()
  const [formError, setFormError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  const {
    register,
    handleSubmit,
    formState: { errors },
  } = useForm<FormValues>({ resolver: zodResolver(schema) })

  const onSubmit = handleSubmit(async (values) => {
    setFormError(null)
    setSubmitting(true)
    try {
      await api.post<void>('/auth/change-password', {
        current_password: values.current_password,
        new_password: values.new_password,
      })
      // Changing the password revokes every other session, including this
      // one's refresh token, so a sign-out and fresh sign-in is the honest
      // next step rather than pretending the session survived.
      await signOut()
      await refreshUser().catch(() => undefined)
    } catch (error) {
      setFormError(describeError(error, t))
    } finally {
      setSubmitting(false)
    }
  })

  return (
    <div className="flex min-h-full items-center justify-center bg-[var(--surface-sunken)] px-4 py-10">
      <div className="w-full max-w-md">
        <Card>
          <h1 className="text-lg font-semibold text-[var(--text-primary)]">
            {t.auth.changePassword}
          </h1>
          <div className="mt-3 mb-5">
            <Alert tone="warning">{t.auth.mustChangePassword}</Alert>
          </div>

          {formError ? (
            <div className="mb-4">
              <Alert tone="danger">{formError}</Alert>
            </div>
          ) : null}

          <form onSubmit={onSubmit} className="flex flex-col gap-4" noValidate>
            <Field
              label={t.auth.currentPassword}
              htmlFor="current_password"
              required
              error={
                errors.current_password ? t.common.required : undefined
              }
            >
              <Input
                id="current_password"
                type="password"
                autoComplete="current-password"
                {...register('current_password')}
              />
            </Field>

            <Field
              label={t.auth.newPassword}
              htmlFor="new_password"
              required
              hint={t.users.initialPasswordHint}
              error={errors.new_password ? t.errors.validationFailed : undefined}
            >
              <Input
                id="new_password"
                type="password"
                autoComplete="new-password"
                {...register('new_password')}
              />
            </Field>

            <Field
              label={t.auth.newPassword}
              htmlFor="confirm_password"
              required
              error={
                errors.confirm_password ? t.errors.validationFailed : undefined
              }
            >
              <Input
                id="confirm_password"
                type="password"
                autoComplete="new-password"
                {...register('confirm_password')}
              />
            </Field>

            <Button type="submit" variant="primary" loading={submitting}>
              {t.auth.changePassword}
            </Button>
            <Button type="button" variant="ghost" onClick={() => void signOut()}>
              {t.auth.signOut}
            </Button>
          </form>
        </Card>
      </div>
    </div>
  )
}
