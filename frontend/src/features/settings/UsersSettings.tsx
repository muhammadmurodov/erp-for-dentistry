import { useState } from 'react'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useI18n } from '@/i18n'
import { useAuth } from '@/features/auth/AuthProvider'
import { api } from '@/lib/api'
import { queryKeys, useOrganizationUsers, useRoles } from '@/lib/queries'
import { describeError, errorRequestId } from '@/lib/errorMessage'
import {
  Alert,
  Badge,
  Button,
  Card,
  CardHeader,
  Checkbox,
  ConfirmDialog,
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
import type { OrganizationUser, Role } from '@/lib/types'
import type { Locale } from '@/i18n'

const createSchema = z.object({
  email: z.string().email(),
  full_name: z.string().min(2).max(255),
  // Mirrors the backend's PASSWORD_MIN_LENGTH so the rule is not a surprise.
  password: z.string().min(12),
  job_title: z.string().max(128).optional().or(z.literal('')),
  locale: z.enum(['uz', 'ru', 'en']),
})

type CreateValues = z.infer<typeof createSchema>

function roleName(role: Role, locale: Locale): string {
  if (locale === 'ru') return role.name_ru
  if (locale === 'en') return role.name_en
  return role.name_uz
}

export function UsersSettings() {
  const { t, locale, formatDateTime } = useI18n()
  const { can, user: currentUser } = useAuth()
  const queryClient = useQueryClient()

  const mayRead = can('identity.user.read')
  const mayManageUsers = can('identity.user.manage')
  const mayManageRoles = can('identity.role.manage')

  const users = useOrganizationUsers(mayRead)
  const roles = useRoles(mayRead)

  const [createOpen, setCreateOpen] = useState(false)
  const [rolesTarget, setRolesTarget] = useState<OrganizationUser | null>(null)
  const [deactivateTarget, setDeactivateTarget] =
    useState<OrganizationUser | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)

  if (!mayRead) {
    return <Alert tone="warning">{t.moduleStatus.noAccess}</Alert>
  }
  if (users.isLoading || roles.isLoading) {
    return <LoadingState label={t.common.loading} />
  }
  if (users.isError) {
    return (
      <ErrorState
        title={t.errors.loadFailedTitle}
        message={describeError(users.error, t)}
        requestId={errorRequestId(users.error)}
        requestIdLabel={t.common.requestId}
        onRetry={() => void users.refetch()}
        retryLabel={t.common.retry}
      />
    )
  }

  const rows = users.data ?? []
  // A role the backend says is not worth offering here is left out rather
  // than shown and then refused. In a three-person clinic that is `director`:
  // the only role whose combination with another breaks a control, and the
  // only one nobody is available to fill (ADR-041). It stays grantable over
  // the API for an organization that grows into it.
  const availableRoles = (roles.data ?? []).filter(
    (role) => role.offered_for_assignment,
  )

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: queryKeys.users })
  }

  return (
    <>
      <Card>
        <CardHeader
          title={t.users.title}
          description={t.users.subtitle}
          actions={
            mayManageUsers ? (
              <Button
                variant="primary"
                size="sm"
                onClick={() => {
                  setActionError(null)
                  setCreateOpen(true)
                }}
              >
                {t.users.addUser}
              </Button>
            ) : undefined
          }
        />

        {notice ? (
          <div className="mb-4">
            <Alert tone="success">{notice}</Alert>
          </div>
        ) : null}
        {actionError ? (
          <div className="mb-4">
            <Alert tone="danger">{actionError}</Alert>
          </div>
        ) : null}

        {rows.length === 0 ? (
          <EmptyState title={t.users.emptyTitle} body={t.users.emptyBody} />
        ) : (
          <Table>
            <thead>
              <tr>
                <Th>{t.users.fullName}</Th>
                <Th>{t.auth.email}</Th>
                <Th>{t.users.jobTitle}</Th>
                <Th>{t.users.roles}</Th>
                <Th>{t.users.lastLogin}</Th>
                <Th>{t.users.status}</Th>
                {mayManageRoles || mayManageUsers ? (
                  <Th>{t.common.actions}</Th>
                ) : null}
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.membership_id}>
                  <Td>
                    <span className="font-medium">{row.full_name}</span>
                    {row.is_primary_owner ? (
                      <span className="ml-2">
                        <Badge tone="brand">{t.users.primaryOwner}</Badge>
                      </span>
                    ) : null}
                    {row.id === currentUser?.id ? (
                      <span className="ml-2 text-xs text-[var(--text-muted)]">
                        ({t.users.you})
                      </span>
                    ) : null}
                  </Td>
                  <Td>
                    <span className="font-mono text-xs">{row.email}</span>
                  </Td>
                  <Td>{row.job_title ?? '—'}</Td>
                  <Td>
                    <div className="flex flex-wrap gap-1">
                      {row.role_codes.length === 0 ? (
                        <span className="text-xs text-[var(--text-muted)]">—</span>
                      ) : (
                        row.role_codes.map((code) => {
                          const role = availableRoles.find((r) => r.code === code)
                          return (
                            <Badge key={code}>
                              {role ? roleName(role, locale) : code}
                            </Badge>
                          )
                        })
                      )}
                    </div>
                  </Td>
                  <Td>
                    {row.last_login_at
                      ? formatDateTime(row.last_login_at)
                      : t.common.never}
                  </Td>
                  <Td>
                    {row.membership_active ? (
                      <Badge tone="success">{t.common.active}</Badge>
                    ) : (
                      <Badge tone="danger">{t.common.inactive}</Badge>
                    )}
                  </Td>
                  {mayManageRoles || mayManageUsers ? (
                    <Td>
                      <div className="flex flex-wrap gap-1.5">
                        {mayManageRoles ? (
                          <Button
                            size="sm"
                            variant="ghost"
                            disabled={row.is_primary_owner}
                            title={
                              row.is_primary_owner
                                ? t.users.primaryOwnerProtected
                                : undefined
                            }
                            onClick={() => {
                              setActionError(null)
                              setRolesTarget(row)
                            }}
                          >
                            {t.users.editRoles}
                          </Button>
                        ) : null}
                        {mayManageUsers && row.membership_active ? (
                          <Button
                            size="sm"
                            variant="ghost"
                            disabled={row.is_primary_owner}
                            title={
                              row.is_primary_owner
                                ? t.users.primaryOwnerProtected
                                : undefined
                            }
                            onClick={() => {
                              setActionError(null)
                              setDeactivateTarget(row)
                            }}
                          >
                            {t.users.deactivate}
                          </Button>
                        ) : null}
                      </div>
                    </Td>
                  ) : null}
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </Card>

      <CreateUserDialog
        open={createOpen}
        roles={availableRoles}
        onClose={() => setCreateOpen(false)}
        onCreated={() => {
          setCreateOpen(false)
          setNotice(t.users.created)
          invalidate()
        }}
        onError={(message) => setActionError(message)}
      />

      <EditRolesDialog
        target={rolesTarget}
        roles={availableRoles}
        onClose={() => setRolesTarget(null)}
        onSaved={() => {
          setRolesTarget(null)
          setNotice(t.users.rolesUpdated)
          invalidate()
        }}
      />

      <DeactivateDialog
        target={deactivateTarget}
        onClose={() => setDeactivateTarget(null)}
        onDone={() => {
          setDeactivateTarget(null)
          setNotice(t.users.deactivated)
          invalidate()
        }}
        onError={(message) => {
          setDeactivateTarget(null)
          setActionError(message)
        }}
      />
    </>
  )
}

function CreateUserDialog({
  open,
  roles,
  onClose,
  onCreated,
  onError,
}: {
  open: boolean
  roles: Role[]
  onClose: () => void
  onCreated: () => void
  onError: (message: string) => void
}) {
  const { t, locale } = useI18n()
  const [selectedRoles, setSelectedRoles] = useState<string[]>([])
  const [formError, setFormError] = useState<string | null>(null)

  const {
    register,
    handleSubmit,
    reset,
    formState: { errors, isSubmitting },
  } = useForm<CreateValues>({
    resolver: zodResolver(createSchema),
    defaultValues: { locale: 'uz' },
  })

  const mutation = useMutation({
    mutationFn: (values: CreateValues) =>
      api.post<OrganizationUser>('/users', {
        email: values.email,
        full_name: values.full_name,
        password: values.password,
        job_title: values.job_title || null,
        locale: values.locale,
        role_codes: selectedRoles,
      }),
    onSuccess: () => {
      reset()
      setSelectedRoles([])
      setFormError(null)
      onCreated()
    },
    onError: (error) => {
      const message = describeError(error, t)
      setFormError(message)
      onError(message)
    },
  })

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={t.users.addUserTitle}
      closeLabel={t.common.close}
      footer={
        <>
          <Button onClick={onClose} disabled={isSubmitting}>
            {t.common.cancel}
          </Button>
          <Button
            variant="primary"
            loading={mutation.isPending}
            onClick={handleSubmit((values) => mutation.mutate(values))}
          >
            {t.common.create}
          </Button>
        </>
      }
    >
      {formError ? (
        <div className="mb-4">
          <Alert tone="danger">{formError}</Alert>
        </div>
      ) : null}

      <form className="flex flex-col gap-4" noValidate>
        <Field
          label={t.users.fullName}
          htmlFor="new-full-name"
          required
          error={errors.full_name ? t.errors.validationFailed : undefined}
        >
          <Input id="new-full-name" {...register('full_name')} />
        </Field>

        <Field
          label={t.auth.email}
          htmlFor="new-email"
          required
          error={errors.email ? t.errors.validationFailed : undefined}
        >
          <Input
            id="new-email"
            type="email"
            spellCheck={false}
            {...register('email')}
          />
        </Field>

        <Field
          label={t.users.initialPassword}
          htmlFor="new-password"
          required
          hint={t.users.initialPasswordHint}
          error={errors.password ? t.errors.validationFailed : undefined}
        >
          <Input
            id="new-password"
            type="password"
            autoComplete="new-password"
            {...register('password')}
          />
        </Field>

        <Field label={t.users.jobTitle} htmlFor="new-job-title">
          <Input id="new-job-title" {...register('job_title')} />
        </Field>

        <Field label={t.users.roles} htmlFor="new-roles">
          <div
            id="new-roles"
            className="max-h-56 overflow-y-auto rounded-lg border border-[var(--border-subtle)] p-1"
          >
            {roles.map((role) => (
              <Checkbox
                key={role.code}
                label={roleName(role, locale)}
                description={role.description ?? undefined}
                checked={selectedRoles.includes(role.code)}
                onChange={(event) =>
                  setSelectedRoles((current) =>
                    event.target.checked
                      ? [...current, role.code]
                      : current.filter((c) => c !== role.code),
                  )
                }
              />
            ))}
          </div>
        </Field>
      </form>
    </Dialog>
  )
}

function EditRolesDialog({
  target,
  roles,
  onClose,
  onSaved,
}: {
  target: OrganizationUser | null
  roles: Role[]
  onClose: () => void
  onSaved: () => void
}) {
  const { t, locale } = useI18n()
  const [selected, setSelected] = useState<string[]>([])
  const [formError, setFormError] = useState<string | null>(null)
  const [initialisedFor, setInitialisedFor] = useState<string | null>(null)

  // Seed the checkboxes from the member's current roles when the dialog opens.
  if (target && initialisedFor !== target.membership_id) {
    setInitialisedFor(target.membership_id)
    setSelected(target.role_codes)
    setFormError(null)
  }

  const mutation = useMutation({
    mutationFn: () =>
      api.put<OrganizationUser>(`/users/${target?.membership_id}/roles`, {
        role_codes: selected,
      }),
    onSuccess: () => {
      setFormError(null)
      onSaved()
    },
    onError: (error) => setFormError(describeError(error, t)),
  })

  return (
    <Dialog
      open={target !== null}
      onClose={onClose}
      title={`${t.users.editRoles}: ${target?.full_name ?? ''}`}
      closeLabel={t.common.close}
      footer={
        <>
          <Button onClick={onClose} disabled={mutation.isPending}>
            {t.common.cancel}
          </Button>
          <Button
            variant="primary"
            loading={mutation.isPending}
            onClick={() => mutation.mutate()}
          >
            {t.common.save}
          </Button>
        </>
      }
    >
      {formError ? (
        <div className="mb-4">
          <Alert tone="danger">{formError}</Alert>
        </div>
      ) : null}
      <p className="mb-3 text-xs text-[var(--text-secondary)]">
        {t.users.rolesHint}
      </p>
      <div className="max-h-72 overflow-y-auto rounded-lg border border-[var(--border-subtle)] p-1">
        {roles.map((role) => (
          <Checkbox
            key={role.code}
            label={
              <span className="flex items-center gap-2">
                {roleName(role, locale)}
                {role.is_read_only ? (
                  <Badge tone="neutral">{t.roles.readOnly}</Badge>
                ) : null}
              </span>
            }
            description={role.description ?? undefined}
            checked={selected.includes(role.code)}
            onChange={(event) =>
              setSelected((current) =>
                event.target.checked
                  ? [...current, role.code]
                  : current.filter((c) => c !== role.code),
              )
            }
          />
        ))}
      </div>
    </Dialog>
  )
}

function DeactivateDialog({
  target,
  onClose,
  onDone,
  onError,
}: {
  target: OrganizationUser | null
  onClose: () => void
  onDone: () => void
  onError: (message: string) => void
}) {
  const { t } = useI18n()
  const mutation = useMutation({
    mutationFn: () =>
      api.post<OrganizationUser>(`/users/${target?.membership_id}/deactivate`),
    onSuccess: onDone,
    onError: (error) => onError(describeError(error, t)),
  })

  return (
    <ConfirmDialog
      open={target !== null}
      onClose={onClose}
      onConfirm={() => mutation.mutate()}
      title={t.users.deactivateTitle}
      body={`${t.users.deactivateBody} — ${target?.full_name ?? ''}`}
      confirmLabel={t.users.deactivate}
      cancelLabel={t.common.cancel}
      closeLabel={t.common.close}
      destructive
      loading={mutation.isPending}
    />
  )
}
