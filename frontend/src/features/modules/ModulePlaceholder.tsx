import { useI18n } from '@/i18n'
import { useModules } from '@/lib/queries'
import { useAuth } from '@/features/auth/AuthProvider'
import {
  Alert,
  Badge,
  Card,
  CardHeader,
  EmptyState,
  LoadingState,
  PageHeader,
} from '@/components/ui'
import type { Dictionary } from '@/i18n/uz'

/**
 * The page shown for a module that is not finished.
 *
 * It states plainly that the section is not built, which phase will deliver
 * it, and exactly which workflows are missing. The alternative — an empty
 * table or a polished screen whose buttons do nothing — would misrepresent
 * the state of the system to the people running the business on it.
 */
export function ModulePlaceholder({
  moduleKey,
  titleKey,
}: {
  moduleKey: string
  titleKey: keyof Dictionary['nav']
}) {
  const { t } = useI18n()
  const { can } = useAuth()
  const modules = useModules()

  if (modules.isLoading) return <LoadingState label={t.common.loading} />

  const info = modules.data?.find((m) => m.key === moduleKey)
  const title = t.nav[titleKey]

  if (!info) {
    return (
      <>
        <PageHeader title={title} />
        <EmptyState title={t.moduleStatus.notAvailableTitle} />
      </>
    )
  }

  if (info.required_permission && !can(info.required_permission)) {
    return (
      <>
        <PageHeader title={title} />
        <Alert tone="warning">{t.moduleStatus.noAccess}</Alert>
      </>
    )
  }

  return (
    <>
      <PageHeader
        title={title}
        description={t.moduleStatus.notAvailableBody}
        actions={
          <Badge tone={info.status === 'partial' ? 'warning' : 'neutral'}>
            {info.status === 'partial'
              ? t.moduleStatus.partial
              : t.moduleStatus.planned}
          </Badge>
        }
      />

      <div
        className={
          info.available_features.length > 0
            ? 'grid gap-4 sm:grid-cols-2'
            : 'grid gap-4'
        }
      >
        {info.available_features.length > 0 ? (
          <Card>
            <CardHeader title={t.moduleStatus.whatWorks} />
            <ul className="flex flex-col gap-2">
              {info.available_features.map((feature) => (
                <li
                  key={feature}
                  className="flex items-start gap-2 text-sm text-[var(--text-primary)]"
                >
                  <span style={{ color: 'var(--success)' }}>✓</span>
                  {feature}
                </li>
              ))}
            </ul>
          </Card>
        ) : null}

        <Card>
          <CardHeader
            title={t.moduleStatus.whatIsMissing}
            description={`${t.moduleStatus.plannedForPhase}: ${info.phase}`}
          />
          <ul className="flex flex-col gap-2">
            {info.missing_features.map((feature) => (
              <li
                key={feature}
                className="flex items-start gap-2 text-sm text-[var(--text-secondary)]"
              >
                <span className="text-[var(--text-muted)]">○</span>
                {feature}
              </li>
            ))}
          </ul>
        </Card>
      </div>
    </>
  )
}
