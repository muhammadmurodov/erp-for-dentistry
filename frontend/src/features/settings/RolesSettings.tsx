import { useMemo, useState } from 'react'
import { useI18n, type Locale } from '@/i18n'
import { useAuth } from '@/features/auth/AuthProvider'
import { usePermissionCatalogue, useRoles } from '@/lib/queries'
import { describeError, errorRequestId } from '@/lib/errorMessage'
import {
  Alert,
  Badge,
  Button,
  Card,
  CardHeader,
  EmptyState,
  ErrorState,
  Input,
  LoadingState,
} from '@/components/ui'
import type { PermissionCatalogEntry, Role } from '@/lib/types'

function roleName(role: Role, locale: Locale): string {
  if (locale === 'ru') return role.name_ru
  if (locale === 'en') return role.name_en
  return role.name_uz
}

/**
 * Read-only view of the roles and what each one grants.
 *
 * Editing a role's permission set is deliberately not here: the shipped roles
 * encode the segregation-of-duties controls the accounting design depends on,
 * and a custom-role editor needs those checks surfaced properly rather than
 * bolted on. It is listed as missing on the settings module.
 */
export function RolesSettings() {
  const { t, locale } = useI18n()
  const { can } = useAuth()
  const mayRead = can('identity.role.read')
  const roles = useRoles(mayRead)
  const catalogue = usePermissionCatalogue(mayRead)
  const [expanded, setExpanded] = useState<string | null>(null)
  const [filter, setFilter] = useState('')

  const byCode = useMemo(() => {
    const map = new Map<string, PermissionCatalogEntry>()
    for (const entry of catalogue.data ?? []) map.set(entry.code, entry)
    return map
  }, [catalogue.data])

  if (!mayRead) return <Alert tone="warning">{t.moduleStatus.noAccess}</Alert>
  if (roles.isLoading) return <LoadingState label={t.common.loading} />
  if (roles.isError) {
    return (
      <ErrorState
        title={t.errors.loadFailedTitle}
        message={describeError(roles.error, t)}
        requestId={errorRequestId(roles.error)}
        requestIdLabel={t.common.requestId}
        onRetry={() => void roles.refetch()}
        retryLabel={t.common.retry}
      />
    )
  }

  const needle = filter.trim().toLowerCase()
  const visible = (roles.data ?? []).filter((role) => {
    if (!needle) return true
    return (
      role.code.includes(needle) ||
      roleName(role, locale).toLowerCase().includes(needle) ||
      role.permission_codes.some((code) => code.includes(needle))
    )
  })

  return (
    <Card>
      <CardHeader
        title={t.roles.title}
        description={t.roles.subtitle}
        actions={
          <>
            <label htmlFor="role-filter" className="sr-only">
              {t.common.search}
            </label>
            <Input
              id="role-filter"
              value={filter}
              onChange={(event) => setFilter(event.target.value)}
              placeholder={t.common.search}
              className="w-44 py-1.5 text-xs"
            />
          </>
        }
      />

      {visible.length === 0 ? (
        <EmptyState title={t.roles.emptyTitle} body={t.common.noResults} />
      ) : (
        <ul className="flex flex-col gap-2">
          {visible.map((role) => {
            const open = expanded === role.id
            return (
              <li
                key={role.id}
                className="rounded-lg border border-[var(--border-subtle)]"
              >
                <div className="flex flex-wrap items-center justify-between gap-3 px-4 py-3">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-sm font-medium text-[var(--text-primary)]">
                        {roleName(role, locale)}
                      </span>
                      <span className="font-mono text-xs text-[var(--text-muted)]">
                        {role.code}
                      </span>
                      {role.is_system ? (
                        <Badge tone="brand">{t.roles.systemRole}</Badge>
                      ) : null}
                      {role.is_read_only ? (
                        <Badge tone="neutral">{t.roles.readOnly}</Badge>
                      ) : null}
                    </div>
                    {role.description ? (
                      <p className="mt-1 text-sm text-[var(--text-secondary)]">
                        {role.description}
                      </p>
                    ) : null}
                  </div>
                  <Button
                    size="sm"
                    variant="ghost"
                    aria-expanded={open}
                    onClick={() => setExpanded(open ? null : role.id)}
                  >
                    {role.permission_codes.length} {t.roles.permissionCount}
                    <span aria-hidden="true">{open ? ' ▲' : ' ▼'}</span>
                  </Button>
                </div>

                {open ? (
                  <div className="border-t border-[var(--border-subtle)] px-4 py-3">
                    <PermissionList
                      codes={role.permission_codes}
                      catalogue={byCode}
                      sensitiveLabel={t.roles.sensitive}
                      loading={catalogue.isLoading}
                      loadingLabel={t.common.loading}
                    />
                  </div>
                ) : null}
              </li>
            )
          })}
        </ul>
      )}
    </Card>
  )
}

function PermissionList({
  codes,
  catalogue,
  sensitiveLabel,
  loading,
  loadingLabel,
}: {
  codes: string[]
  catalogue: Map<string, PermissionCatalogEntry>
  sensitiveLabel: string
  loading: boolean
  loadingLabel: string
}) {
  if (loading) return <LoadingState label={loadingLabel} />

  // Group by module so a 67-permission owner role stays readable.
  const grouped = new Map<string, string[]>()
  for (const code of [...codes].sort()) {
    const moduleName = catalogue.get(code)?.module ?? code.split('.')[0] ?? 'other'
    const list = grouped.get(moduleName) ?? []
    list.push(code)
    grouped.set(moduleName, list)
  }

  return (
    <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
      {[...grouped.entries()].map(([moduleName, moduleCodes]) => (
        <div key={moduleName}>
          <p className="mb-1.5 text-xs font-semibold tracking-wide text-[var(--text-secondary)] uppercase">
            {moduleName}
          </p>
          <ul className="flex flex-col gap-1">
            {moduleCodes.map((code) => {
              const entry = catalogue.get(code)
              return (
                <li key={code} className="text-xs">
                  <span className="font-mono text-[var(--text-primary)]">
                    {code.split('.').slice(1).join('.')}
                  </span>
                  {entry?.is_sensitive ? (
                    <span
                      className="ml-1.5"
                      title={sensitiveLabel}
                      aria-label={sensitiveLabel}
                      style={{ color: 'var(--warning)' }}
                    >
                      ●
                    </span>
                  ) : null}
                  {entry ? (
                    <span className="block text-[var(--text-muted)]">
                      {entry.description}
                    </span>
                  ) : null}
                </li>
              )
            })}
          </ul>
        </div>
      ))}
    </div>
  )
}
