import { useI18n } from '@/i18n'
import { useModules } from '@/lib/queries'
import { describeError, errorRequestId } from '@/lib/errorMessage'
import {
  Alert,
  Badge,
  Card,
  CardHeader,
  ErrorState,
  LoadingState,
} from '@/components/ui'
import { NAV_ENTRIES } from '@/lib/navigation'
import type { ModuleInfo, ModuleStatus } from '@/lib/types'

/**
 * What this installation can and cannot do, module by module.
 *
 * This used to sit on the dashboard, where it competed with the clinic's
 * actual work for the most valuable space on the screen. It is an
 * administrator's page: somebody asks it once, when they want to know whether
 * a feature exists yet, and then does not look again for a month.
 *
 * **Every label here comes from the backend registry** — `status`,
 * `available_features`, `missing_features`. Nothing is hardcoded in this
 * file, and there is deliberately no mapping that could be edited to make a
 * module look finished. A module reads as fully available when the registry
 * says `available`, and the registry says that only when the documented
 * workflow is built, authorized and tested. Relabelling "partial" as "ready"
 * to tidy up the interface would be a lie told to the people running a
 * business on it.
 */
export function SystemStatus() {
  const { t } = useI18n()
  const modules = useModules()

  if (modules.isLoading) return <LoadingState label={t.common.loading} />
  if (modules.isError) {
    return (
      <ErrorState
        title={t.errors.loadFailedTitle}
        message={describeError(modules.error, t)}
        requestId={errorRequestId(modules.error)}
        requestIdLabel={t.common.requestId}
        onRetry={() => void modules.refetch()}
        retryLabel={t.common.retry}
      />
    )
  }

  const all = modules.data ?? []
  // The navigation's order, so this page and the menu agree about what the
  // system contains; anything the registry reports that the menu does not
  // list is appended rather than dropped.
  const ordered = [
    ...NAV_ENTRIES.map((entry) => all.find((m) => m.key === entry.key)).filter(
      (m): m is ModuleInfo => Boolean(m),
    ),
    ...all.filter((m) => !NAV_ENTRIES.some((e) => e.key === m.key)),
  ]
  const counts = {
    available: ordered.filter((m) => m.status === 'available').length,
    partial: ordered.filter((m) => m.status === 'partial').length,
    planned: ordered.filter((m) => m.status === 'planned').length,
  }

  return (
    <div className="space-y-5">
      <Card>
        <CardHeader
          title={t.systemStatus.title}
          description={t.systemStatus.description}
        />
        <div className="flex flex-wrap gap-2 px-4 pb-4">
          <Badge tone="success">
            {t.systemStatus.countAvailable.replace('{n}', String(counts.available))}
          </Badge>
          <Badge tone="warning">
            {t.systemStatus.countPartial.replace('{n}', String(counts.partial))}
          </Badge>
          <Badge tone="neutral">
            {t.systemStatus.countPlanned.replace('{n}', String(counts.planned))}
          </Badge>
        </div>
        <Alert tone="info">{t.systemStatus.sourceNote}</Alert>
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        {ordered.map((module) => (
          <ModuleCard key={module.key} module={module} />
        ))}
      </div>
    </div>
  )
}

function ModuleCard({ module }: { module: ModuleInfo }) {
  const { t } = useI18n()
  const entry = NAV_ENTRIES.find((e) => e.key === module.key)
  const name = entry ? entry.label(t) : module.key

  return (
    <Card>
      <CardHeader
        title={
          <span className="flex flex-wrap items-center gap-2">
            {name}
            <StatusBadge status={module.status} />
            {!module.visible_to_caller ? (
              <Badge tone="neutral">{t.systemStatus.notYours}</Badge>
            ) : null}
          </span>
        }
        description={t.systemStatus.phase.replace('{n}', String(module.phase))}
      />
      <div className="space-y-3 p-4 pt-0 text-sm">
        {module.available_features.length > 0 ? (
          <FeatureList
            heading={t.systemStatus.worksNow}
            items={module.available_features}
            tone="available"
          />
        ) : null}
        {module.missing_features.length > 0 ? (
          <FeatureList
            heading={t.systemStatus.notYet}
            items={module.missing_features}
            tone="missing"
          />
        ) : null}
        {module.available_features.length === 0 &&
        module.missing_features.length === 0 ? (
          <p className="text-[var(--text-secondary)]">
            {t.systemStatus.nothingDeclared}
          </p>
        ) : null}
        {module.required_permission ? (
          <p className="text-xs text-[var(--text-secondary)]">
            {t.systemStatus.requiresPermission}:{' '}
            <code className="rounded bg-[var(--surface-sunken)] px-1 py-0.5">
              {module.required_permission}
            </code>
          </p>
        ) : null}
      </div>
    </Card>
  )
}

function FeatureList({
  heading,
  items,
  tone,
}: {
  heading: string
  items: string[]
  tone: 'available' | 'missing'
}) {
  return (
    <div>
      <p className="mb-1 text-xs font-medium uppercase tracking-wide text-[var(--text-secondary)]">
        {heading}
      </p>
      <ul className="space-y-1">
        {items.map((item) => (
          <li key={item} className="flex gap-2">
            <span
              aria-hidden
              className={
                tone === 'available'
                  ? 'text-[var(--success)]'
                  : 'text-[var(--text-secondary)]'
              }
            >
              {tone === 'available' ? '✓' : '·'}
            </span>
            {/*
              The registry's own wording, verbatim. These strings cite open
              questions by number ("OPEN_QUESTIONS A5"), which is exactly the
              honesty ADR-015 put them there for — and they are English prose
              in every language, which is a known gap recorded in
              IMPLEMENTATION_STATUS.md rather than a surprise.
            */}
            <span className="text-[var(--text-primary)]">{item}</span>
          </li>
        ))}
      </ul>
    </div>
  )
}

function StatusBadge({ status }: { status: ModuleStatus }) {
  const { t } = useI18n()
  if (status === 'available')
    return <Badge tone="success">{t.moduleStatus.available}</Badge>
  if (status === 'partial')
    return <Badge tone="warning">{t.moduleStatus.partial}</Badge>
  return <Badge tone="neutral">{t.moduleStatus.planned}</Badge>
}
