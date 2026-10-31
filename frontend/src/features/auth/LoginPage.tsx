import { useState } from 'react'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'
import { useAuth } from './AuthProvider'
import { useI18n } from '@/i18n'
import { Alert, Button, Card, Field, Input, Select } from '@/components/ui'
import { ApiError } from '@/lib/api'
import { describeError } from '@/lib/errorMessage'
import { LocaleSwitcher } from '@/components/LocaleSwitcher'

const schema = z.object({
  email: z.string().min(1).email(),
  password: z.string().min(1),
  organizationId: z.string().optional(),
})

type FormValues = z.infer<typeof schema>

export function LoginPage() {
  const { t } = useI18n()
  const { signIn, expiredNotice, dismissExpiredNotice } = useAuth()
  const [formError, setFormError] = useState<string | null>(null)
  /**
   * Populated when the backend answers "choose an organization": a user with
   * more than one membership must say which books they are opening.
   */
  const [organizationOptions, setOrganizationOptions] = useState<
    { id: string }[] | null
  >(null)

  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
    setError,
  } = useForm<FormValues>({ resolver: zodResolver(schema) })

  const onSubmit = handleSubmit(async (values) => {
    setFormError(null)
    dismissExpiredNotice()
    try {
      await signIn({
        email: values.email,
        password: values.password,
        organizationId: values.organizationId || undefined,
      })
    } catch (error) {
      if (error instanceof ApiError) {
        // The backend asks for an organization by returning the options.
        const options = error.details.options
        if (error.code === 'validation_failed' && Array.isArray(options)) {
          setOrganizationOptions(options as { id: string }[])
          setError('organizationId', { message: t.auth.selectOrganization })
          setFormError(t.auth.selectOrganization)
          return
        }
        const fields = error.fieldErrors()
        if (fields.email) setError('email', { message: fields.email })
        if (fields.password) setError('password', { message: fields.password })
      }
      setFormError(describeError(error, t))
    }
  })

  return (
    <div className="flex min-h-full items-center justify-center bg-[var(--surface-sunken)] px-4 py-10">
      <div className="w-full max-w-md">
        <div className="mb-6 flex items-center justify-between">
          <div className="flex items-center gap-2.5">
            <Logo />
            <span className="text-lg font-semibold text-[var(--text-primary)]">
              {t.common.appName}
            </span>
          </div>
          <LocaleSwitcher />
        </div>

        <Card>
          <h1 className="text-lg font-semibold text-[var(--text-primary)]">
            {t.auth.signInTitle}
          </h1>
          <p className="mt-1 mb-5 text-sm text-[var(--text-secondary)]">
            {t.auth.signInSubtitle}
          </p>

          {expiredNotice ? (
            <div className="mb-4">
              <Alert tone="warning">{t.auth.sessionExpired}</Alert>
            </div>
          ) : null}

          {formError ? (
            <div className="mb-4">
              <Alert tone="danger">{formError}</Alert>
            </div>
          ) : null}

          <form onSubmit={onSubmit} className="flex flex-col gap-4" noValidate>
            <Field
              label={t.auth.email}
              htmlFor="email"
              required
              error={errors.email ? t.common.required : undefined}
            >
              <Input
                id="email"
                type="email"
                autoComplete="username"
                autoFocus
                spellCheck={false}
                {...register('email')}
              />
            </Field>

            <Field
              label={t.auth.password}
              htmlFor="password"
              required
              error={errors.password ? t.common.required : undefined}
            >
              <Input
                id="password"
                type="password"
                autoComplete="current-password"
                {...register('password')}
              />
            </Field>

            {organizationOptions ? (
              <Field
                label={t.auth.organization}
                htmlFor="organizationId"
                required
                error={errors.organizationId?.message}
              >
                <Select id="organizationId" {...register('organizationId')}>
                  <option value="">{t.auth.selectOrganization}</option>
                  {organizationOptions.map((option) => (
                    <option key={option.id} value={option.id}>
                      {option.id}
                    </option>
                  ))}
                </Select>
              </Field>
            ) : null}

            <Button type="submit" variant="primary" loading={isSubmitting}>
              {isSubmitting ? t.auth.signingIn : t.auth.signIn}
            </Button>
          </form>
        </Card>
      </div>
    </div>
  )
}

function Logo() {
  return (
    <svg width="28" height="28" viewBox="0 0 32 32" aria-hidden="true">
      <rect width="32" height="32" rx="8" fill="var(--color-brand-600)" />
      <path
        d="M10 10.5h12M10 16h12M10 21.5h7"
        stroke="white"
        strokeWidth="2.2"
        strokeLinecap="round"
      />
    </svg>
  )
}
