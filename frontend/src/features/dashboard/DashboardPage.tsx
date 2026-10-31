import { useI18n } from '@/i18n'
import { useAuth } from '@/features/auth/AuthProvider'
import { useModules, useOrganization } from '@/lib/queries'
import {
  Alert,
  Badge,
  Card,
  CardHeader,
  DescriptionList,
  ErrorState,
  LoadingState,
  PageHeader,
} from '@/components/ui'
import { describeError, errorRequestId } from '@/lib/errorMessage'
import { NAV_ENTRIES } from '@/lib/navigation'
import type { ModuleStatus } from '@/lib/types'

export function DashboardPage() {
  const { t, formatDate } = useI18n()
  const { user } = useAuth()
  const organization = useOrganization()
  const modules = useModules()

  return (
    <>
      <PageHeader
        title={t.dashboard.title}
        description={`${t.dashboard.welcome}, ${user?.full_name ?? ''}`}
      />

      {/*
        Stated up front rather than filled with placeholder numbers: an
        invented figure on a finance dashboard is worse than an absent one.
      */}
      <div className="mb-5">
        <Alert tone="info">{t.dashboard.noFinancialDataYet}</Alert>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader title={t.dashboard.organizationCard} />
          {organization.isLoading ? (
            <LoadingState label={t.common.loading} />
          ) : organization.isError ? (
            <ErrorState
              title={t.errors.loadFailedTitle}
              message={describeError(organization.error, t)}
              requestId={errorRequestId(organization.error)}
              requestIdLabel={t.common.requestId}
              onRetry={() => void organization.refetch()}
              retryLabel={t.common.retry}
            />
          ) : organization.data ? (
            <DescriptionList
              items={[
                {
                  label: t.organization.legalName,
                  value: organization.data.legal_name,
                },
                {
                  label: t.organization.taxNumber,
                  value: organization.data.tax_number ?? '—',
                },
                {
                  label: t.organization.activity,
                  value: organization.data.activity_description ?? '—',
                },
                {
                  label: t.organization.baseCurrency,
                  value: organization.data.base_currency,
                },
                {
                  label: t.organization.registeredOn,
                  value: formatDate(organization.data.registered_on),
                },
              ]}
            />
          ) : null}
        </Card>

        <Card>
          <CardHeader
            title={t.dashboard.implementationCard}
            description={t.dashboard.implementationHint}
          />
          {modules.isLoading ? (
            <LoadingState label={t.common.loading} />
          ) : (
            <ul className="flex flex-col divide-y divide-[var(--border-subtle)]">
              {NAV_ENTRIES.map((entry) => {
                const info = modules.data?.find((m) => m.key === entry.key)
                // Match the navigation: a module the caller has no permission
                // for is not listed here either, or the two disagree about
                // what this person's system contains.
                if (!info || !info.visible_to_caller) return null
                return (
                  <li
                    key={entry.key}
                    className="flex items-center justify-between gap-3 py-2"
                  >
                    <span className="text-sm text-[var(--text-primary)]">
                      {entry.label(t)}
                    </span>
                    <StatusBadge status={info.status} />
                  </li>
                )
              })}
            </ul>
          )}
        </Card>
      </div>
    </>
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
