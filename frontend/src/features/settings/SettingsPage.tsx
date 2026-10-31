import { NavLink, Navigate, Route, Routes } from 'react-router-dom'
import clsx from 'clsx'
import { useI18n } from '@/i18n'
import { useAuth } from '@/features/auth/AuthProvider'
import { PageHeader } from '@/components/ui'
import { OrganizationSettings } from './OrganizationSettings'
import { UsersSettings } from './UsersSettings'
import { RolesSettings } from './RolesSettings'
import { AuditSettings } from './AuditSettings'

export function SettingsPage() {
  const { t } = useI18n()
  const { can } = useAuth()

  // Only tabs the user can actually open are offered.
  const tabs = [
    {
      to: 'organization',
      label: t.settings.organizationTab,
      visible: can('organization.read'),
    },
    {
      to: 'users',
      label: t.settings.usersTab,
      visible: can('identity.user.read'),
    },
    {
      to: 'roles',
      label: t.settings.rolesTab,
      visible: can('identity.role.read'),
    },
    { to: 'audit', label: t.settings.auditTab, visible: can('audit.log.read') },
  ].filter((tab) => tab.visible)

  const first = tabs[0]?.to ?? 'organization'

  return (
    <>
      <PageHeader title={t.settings.title} />

      <nav aria-label={t.settings.title} className="mb-5">
        <ul className="flex flex-wrap gap-1 border-b border-[var(--border-subtle)]">
          {tabs.map((tab) => (
            <li key={tab.to}>
              <NavLink
                to={tab.to}
                className={({ isActive }) =>
                  clsx(
                    '-mb-px inline-block border-b-2 px-3 py-2 text-sm',
                    isActive
                      ? 'border-brand-600 font-medium text-brand-700'
                      : 'border-transparent text-[var(--text-secondary)] hover:text-[var(--text-primary)]',
                  )
                }
              >
                {tab.label}
              </NavLink>
            </li>
          ))}
        </ul>
      </nav>

      {/*
        Only permitted tabs get a route. A stale or hand-typed path to a tab
        this user cannot open redirects to their first available one, rather
        than leaving them on a page that only says "no access".
      */}
      <Routes>
        <Route index element={<Navigate to={first} replace />} />
        {can('organization.read') ? (
          <Route path="organization" element={<OrganizationSettings />} />
        ) : null}
        {can('identity.user.read') ? (
          <Route path="users" element={<UsersSettings />} />
        ) : null}
        {can('identity.role.read') ? (
          <Route path="roles" element={<RolesSettings />} />
        ) : null}
        {can('audit.log.read') ? (
          <Route path="audit" element={<AuditSettings />} />
        ) : null}
        <Route path="*" element={<Navigate to={first} replace />} />
      </Routes>
    </>
  )
}
