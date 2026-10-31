import { useState } from 'react'
import { NavLink, Outlet, useLocation } from 'react-router-dom'
import clsx from 'clsx'
import { useAuth } from '@/features/auth/AuthProvider'
import { useI18n } from '@/i18n'
import { useModules } from '@/lib/queries'
import { NAV_ENTRIES } from '@/lib/navigation'
import { Icon } from './Icon'
import { LocaleSwitcher } from './LocaleSwitcher'
import { ThemeSwitcher } from './ThemeSwitcher'
import { Badge, Button, Select, Spinner } from './ui'
import type { ModuleStatus } from '@/lib/types'

export function AppShell() {
  const { t } = useI18n()
  const { user, signOut, switchOrganization } = useAuth()
  const modules = useModules()
  const [mobileOpen, setMobileOpen] = useState(false)
  const location = useLocation()

  const statusByKey = new Map<string, ModuleStatus>(
    modules.data?.map((m) => [m.key, m.status]) ?? [],
  )
  const visibleByKey = new Map<string, boolean>(
    modules.data?.map((m) => [m.key, m.visible_to_caller]) ?? [],
  )

  // Until the registry has loaded, show every entry rather than flashing a
  // short menu that then grows.
  const entries = NAV_ENTRIES.filter(
    (entry) => visibleByKey.get(entry.key) ?? true,
  )

  const activeMembership = user?.active_membership

  return (
    <div className="flex min-h-full flex-col lg:flex-row">
      {/* --- sidebar --- */}
      <aside
        className={clsx(
          'border-b border-[var(--border-subtle)] bg-[var(--surface-raised)] lg:w-64 lg:shrink-0 lg:border-r lg:border-b-0',
          mobileOpen ? 'block' : 'hidden lg:block',
        )}
      >
        <div className="sticky top-0 flex h-full flex-col">
          <div className="hidden items-center gap-2.5 px-4 py-4 lg:flex">
            <Mark />
            <span className="text-sm font-semibold text-[var(--text-primary)]">
              {t.common.appName}
            </span>
          </div>

          <nav aria-label={t.nav.dashboard} className="flex-1 px-2 pb-4">
            <ul className="flex flex-col gap-0.5">
              {entries.map((entry) => {
                const status = statusByKey.get(entry.key)
                return (
                  <li key={entry.key}>
                    <NavLink
                      to={entry.path}
                      end={entry.path === '/'}
                      onClick={() => setMobileOpen(false)}
                      className={({ isActive }) =>
                        clsx(
                          'flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm',
                          isActive
                            ? 'bg-brand-50 font-medium text-brand-800'
                            : 'text-[var(--text-secondary)] hover:bg-[var(--surface-sunken)]',
                        )
                      }
                    >
                      <Icon name={entry.icon} />
                      <span className="min-w-0 flex-1 truncate">
                        {entry.label(t)}
                      </span>
                      {/*
                        An unfinished module is marked right in the menu, so
                        nobody opens it expecting a working screen.
                      */}
                      {status === 'planned' ? (
                        <span
                          title={t.moduleStatus.planned}
                          aria-label={t.moduleStatus.planned}
                          className="size-1.5 shrink-0 rounded-full"
                          style={{ background: 'var(--text-muted)' }}
                        />
                      ) : status === 'partial' ? (
                        <span
                          title={t.moduleStatus.partial}
                          aria-label={t.moduleStatus.partial}
                          className="size-1.5 shrink-0 rounded-full"
                          style={{ background: 'var(--warning)' }}
                        />
                      ) : null}
                    </NavLink>
                  </li>
                )
              })}
            </ul>
          </nav>

          <div className="border-t border-[var(--border-subtle)] px-3 py-3">
            {/* A key for the dots beside each navigation entry. */}
            <ul className="mb-2 flex flex-wrap gap-x-3 gap-y-1">
              <li className="flex items-center gap-1.5 text-xs text-[var(--text-muted)]">
                <span
                  className="size-1.5 rounded-full"
                  style={{ background: 'var(--warning)' }}
                />
                {t.moduleStatus.partial}
              </li>
              <li className="flex items-center gap-1.5 text-xs text-[var(--text-muted)]">
                <span
                  className="size-1.5 rounded-full"
                  style={{ background: 'var(--text-muted)' }}
                />
                {t.moduleStatus.planned}
              </li>
            </ul>
            <div className="flex flex-wrap gap-2">
              <LocaleSwitcher />
              <ThemeSwitcher />
            </div>
          </div>
        </div>
      </aside>

      {/* --- main --- */}
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex items-center gap-3 border-b border-[var(--border-subtle)] bg-[var(--surface-raised)] px-4 py-3">
          <button
            type="button"
            className="rounded-lg border border-[var(--border-strong)] px-2.5 py-1.5 text-sm lg:hidden"
            aria-expanded={mobileOpen}
            onClick={() => setMobileOpen((open) => !open)}
          >
            ☰
          </button>

          <div className="min-w-0 flex-1">
            {activeMembership ? (
              <div className="flex min-w-0 items-center gap-2">
                <span className="truncate text-sm font-medium text-[var(--text-primary)]">
                  {activeMembership.organization_name}
                </span>
                {activeMembership.is_primary_owner ? (
                  <Badge tone="brand">{t.users.primaryOwner}</Badge>
                ) : null}
              </div>
            ) : (
              <span className="text-sm text-[var(--text-muted)]">
                {t.auth.noOrganization}
              </span>
            )}
          </div>

          {/* Only shown when there is actually somewhere to switch to. */}
          {user && user.memberships.length > 1 ? (
            <>
              <label htmlFor="org-switcher" className="sr-only">
                {t.auth.switchOrganization}
              </label>
              <Select
                id="org-switcher"
                value={activeMembership?.organization_id ?? ''}
                onChange={(event) => {
                  void switchOrganization(event.target.value)
                }}
                className="w-auto py-1.5 text-xs"
              >
                {user.memberships.map((membership) => (
                  <option
                    key={membership.organization_id}
                    value={membership.organization_id}
                  >
                    {membership.organization_name}
                  </option>
                ))}
              </Select>
            </>
          ) : null}

          <div className="hidden text-right sm:block">
            <p className="text-sm text-[var(--text-primary)]">
              {user?.full_name}
            </p>
            <p className="text-xs text-[var(--text-muted)]">
              {activeMembership?.role_codes.join(', ') || user?.email}
            </p>
          </div>

          <Button size="sm" onClick={() => void signOut()}>
            {t.auth.signOut}
          </Button>
        </header>

        <main className="min-w-0 flex-1 px-4 py-6 sm:px-6">
          {modules.isLoading ? (
            <div className="flex items-center gap-2 text-sm text-[var(--text-secondary)]">
              <Spinner /> {t.common.loading}
            </div>
          ) : (
            <div key={location.pathname} className="mx-auto max-w-6xl">
              <Outlet />
            </div>
          )}
        </main>
      </div>
    </div>
  )
}

function Mark() {
  return (
    <svg width="26" height="26" viewBox="0 0 32 32" aria-hidden="true">
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
