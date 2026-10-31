import { useEffect, useState } from 'react'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useI18n } from '@/i18n'
import { useAuth } from '@/features/auth/AuthProvider'
import { api, ApiError } from '@/lib/api'
import { queryKeys, useCurrencies, useOrganization } from '@/lib/queries'
import { describeError, errorRequestId } from '@/lib/errorMessage'
import {
  Alert,
  Button,
  Card,
  CardHeader,
  ErrorState,
  Field,
  Input,
  LoadingState,
  Select,
  Textarea,
} from '@/components/ui'
import type { Organization } from '@/lib/types'

const schema = z.object({
  legal_name: z.string().min(2).max(255),
  short_name: z.string().max(128).optional().or(z.literal('')),
  // Matches the backend's own constraint, so the form rejects what the API
  // would reject rather than letting the user discover it on submit.
  tax_number: z
    .string()
    .regex(/^[0-9]{9,14}$/)
    .optional()
    .or(z.literal('')),
  legal_form: z.string().max(32).optional().or(z.literal('')),
  activity_description: z.string().optional().or(z.literal('')),
  address: z.string().optional().or(z.literal('')),
  phone: z.string().max(32).optional().or(z.literal('')),
  email: z.string().email().optional().or(z.literal('')),
  registered_on: z.string().optional().or(z.literal('')),
  timezone: z.string().max(64),
  default_locale: z.enum(['uz', 'ru', 'en']),
})

type FormValues = z.infer<typeof schema>

export function OrganizationSettings() {
  const { t } = useI18n()
  const { can } = useAuth()
  const queryClient = useQueryClient()
  const organization = useOrganization()
  const currencies = useCurrencies()
  const [saved, setSaved] = useState(false)
  const [submitError, setSubmitError] = useState<string | null>(null)

  const editable = can('organization.manage')

  const {
    register,
    handleSubmit,
    reset,
    setError,
    formState: { errors, isDirty },
  } = useForm<FormValues>({ resolver: zodResolver(schema) })

  // Populate the form once the record arrives, and after a successful save.
  useEffect(() => {
    if (!organization.data) return
    const data = organization.data
    reset({
      legal_name: data.legal_name,
      short_name: data.short_name ?? '',
      tax_number: data.tax_number ?? '',
      legal_form: data.legal_form ?? '',
      activity_description: data.activity_description ?? '',
      address: data.address ?? '',
      phone: data.phone ?? '',
      email: data.email ?? '',
      registered_on: data.registered_on ?? '',
      timezone: data.timezone,
      default_locale: (data.default_locale as 'uz' | 'ru' | 'en') ?? 'uz',
    })
  }, [organization.data, reset])

  const mutation = useMutation({
    mutationFn: (values: FormValues) => {
      // Empty strings mean "clear the field"; send null rather than ''.
      const body: Record<string, unknown> = {}
      for (const [key, value] of Object.entries(values)) {
        body[key] = value === '' ? null : value
      }
      return api.patch<Organization>('/organization', body)
    },
    onSuccess: (updated) => {
      queryClient.setQueryData(queryKeys.organization, updated)
      setSaved(true)
      setSubmitError(null)
    },
    onError: (error) => {
      setSaved(false)
      // Surface the backend's own field errors next to the fields.
      if (error instanceof ApiError) {
        for (const [field, message] of Object.entries(error.fieldErrors())) {
          if (field in schema.shape) {
            setError(field as keyof FormValues, { message })
          }
        }
      }
      setSubmitError(describeError(error, t))
    },
  })

  if (organization.isLoading) return <LoadingState label={t.common.loading} />
  if (organization.isError) {
    return (
      <ErrorState
        title={t.errors.loadFailedTitle}
        message={describeError(organization.error, t)}
        requestId={errorRequestId(organization.error)}
        requestIdLabel={t.common.requestId}
        onRetry={() => void organization.refetch()}
        retryLabel={t.common.retry}
      />
    )
  }

  const uzs = currencies.data?.find(
    (c) => c.code === organization.data?.base_currency,
  )

  return (
    <Card>
      <CardHeader
        title={t.organization.title}
        description={
          editable ? undefined : t.moduleStatus.noAccess
        }
      />

      {saved ? (
        <div className="mb-4">
          <Alert tone="success">{t.organization.saved}</Alert>
        </div>
      ) : null}
      {submitError ? (
        <div className="mb-4">
          <Alert tone="danger">{submitError}</Alert>
        </div>
      ) : null}

      <form
        onSubmit={handleSubmit((values) => {
          setSaved(false)
          mutation.mutate(values)
        })}
        className="grid gap-4 sm:grid-cols-2"
        noValidate
      >
        <fieldset disabled={!editable} className="contents">
          <div className="sm:col-span-2">
            <Field
              label={t.organization.legalName}
              htmlFor="legal_name"
              required
              error={errors.legal_name ? t.errors.validationFailed : undefined}
            >
              <Input id="legal_name" {...register('legal_name')} />
            </Field>
          </div>

          <Field label={t.organization.shortName} htmlFor="short_name">
            <Input id="short_name" {...register('short_name')} />
          </Field>

          <Field
            label={t.organization.taxNumber}
            htmlFor="tax_number"
            hint={t.organization.taxNumberHint}
            error={errors.tax_number ? t.organization.taxNumberHint : undefined}
          >
            <Input
              id="tax_number"
              inputMode="numeric"
              {...register('tax_number')}
            />
          </Field>

          <Field label={t.organization.legalForm} htmlFor="legal_form">
            <Input id="legal_form" {...register('legal_form')} />
          </Field>

          <Field label={t.organization.registeredOn} htmlFor="registered_on">
            <Input id="registered_on" type="date" {...register('registered_on')} />
          </Field>

          <div className="sm:col-span-2">
            <Field
              label={t.organization.activity}
              htmlFor="activity_description"
            >
              <Textarea
                id="activity_description"
                {...register('activity_description')}
              />
            </Field>
          </div>

          <div className="sm:col-span-2">
            <Field label={t.organization.address} htmlFor="address">
              <Textarea id="address" {...register('address')} />
            </Field>
          </div>

          <Field label={t.organization.phone} htmlFor="phone">
            <Input id="phone" type="tel" {...register('phone')} />
          </Field>

          <Field
            label={t.organization.email}
            htmlFor="email"
            error={errors.email ? t.errors.validationFailed : undefined}
          >
            <Input id="email" type="email" {...register('email')} />
          </Field>

          <Field label={t.organization.timezone} htmlFor="timezone">
            <Input id="timezone" {...register('timezone')} />
          </Field>

          <Field label={t.organization.locale} htmlFor="default_locale">
            <Select id="default_locale" {...register('default_locale')}>
              <option value="uz">O‘zbekcha</option>
              <option value="ru">Русский</option>
              <option value="en">English</option>
            </Select>
          </Field>
        </fieldset>

        {/*
          Code and base currency are read-only: both are referenced by posted
          accounting records, so changing one would reinterpret history.
        */}
        <div className="grid gap-4 sm:col-span-2 sm:grid-cols-2">
          <Field label={t.organization.code} htmlFor="code-readonly">
            <Input
              id="code-readonly"
              value={organization.data?.code ?? ''}
              readOnly
              disabled
            />
          </Field>
          <Field
            label={t.organization.baseCurrency}
            htmlFor="currency-readonly"
            hint={
              uzs
                ? `${uzs.name_uz} · ${uzs.decimal_places} decimals`
                : undefined
            }
          >
            <Input
              id="currency-readonly"
              value={organization.data?.base_currency ?? ''}
              readOnly
              disabled
            />
          </Field>
        </div>

        {editable ? (
          <div className="flex gap-2 sm:col-span-2">
            <Button
              type="submit"
              variant="primary"
              loading={mutation.isPending}
              disabled={!isDirty}
            >
              {mutation.isPending ? t.common.saving : t.common.save}
            </Button>
            <Button
              type="button"
              onClick={() => {
                reset()
                setSaved(false)
                setSubmitError(null)
              }}
              disabled={!isDirty || mutation.isPending}
            >
              {t.common.cancel}
            </Button>
          </div>
        ) : null}
      </form>
    </Card>
  )
}
