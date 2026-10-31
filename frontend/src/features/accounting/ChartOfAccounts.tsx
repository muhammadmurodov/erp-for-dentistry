import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useI18n } from '@/i18n'
import { useAuth } from '@/features/auth/AuthProvider'
import { api } from '@/lib/api'
import { useAccounts } from '@/lib/queries'
import { describeError, errorRequestId } from '@/lib/errorMessage'
import {
  Alert,
  Badge,
  Button,
  Card,
  CardHeader,
  ConfirmDialog,
  EmptyState,
  ErrorState,
  Input,
  LoadingState,
  Table,
  Td,
  Th,
} from '@/components/ui'
import type { Account, SeedChartResult } from '@/lib/types'

export function ChartOfAccounts() {
  const { t } = useI18n()
  const { can } = useAuth()
  const queryClient = useQueryClient()
  const [search, setSearch] = useState('')
  const [seedOpen, setSeedOpen] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const mayManage = can('accounting.account.manage')
  const accounts = useAccounts(can('accounting.account.read'), {
    search: search.trim() || undefined,
    active_only: true,
  })

  const seed = useMutation({
    mutationFn: () =>
      api.post<SeedChartResult>('/accounting/accounts/seed-statutory-chart', {
        confirm: true,
      }),
    onSuccess: (result) => {
      setSeedOpen(false)
      setError(null)
      setNotice(`${t.accounting.seeded} ${result.accounts} — ${result.notice}`)
      void queryClient.invalidateQueries({ queryKey: ['accounting'] })
    },
    onError: (err) => {
      setSeedOpen(false)
      setError(describeError(err, t))
    },
  })

  if (!can('accounting.account.read')) {
    return <Alert tone="warning">{t.moduleStatus.noAccess}</Alert>
  }
  if (accounts.isLoading) return <LoadingState label={t.common.loading} />
  if (accounts.isError) {
    return (
      <ErrorState
        title={t.errors.loadFailedTitle}
        message={describeError(accounts.error, t)}
        requestId={errorRequestId(accounts.error)}
        requestIdLabel={t.common.requestId}
        onRetry={() => void accounts.refetch()}
        retryLabel={t.common.retry}
      />
    )
  }

  const rows = accounts.data ?? []
  const unconfirmed = rows.filter((a) => a.needs_confirmation).length

  return (
    <>
      <Card>
        <CardHeader
          title={t.accounting.chartTab}
          description={`${rows.length} ${t.accounting.name.toLowerCase()}`}
          actions={
            <>
              <label htmlFor="account-search" className="sr-only">
                {t.common.search}
              </label>
              <Input
                id="account-search"
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                placeholder={t.common.search}
                className="w-44 py-1.5 text-xs"
              />
              {mayManage && rows.length === 0 ? (
                <Button
                  variant="primary"
                  size="sm"
                  onClick={() => setSeedOpen(true)}
                >
                  {t.accounting.seedChart}
                </Button>
              ) : null}
            </>
          }
        />

        {notice ? (
          <div className="mb-4">
            <Alert tone="success">{notice}</Alert>
          </div>
        ) : null}
        {error ? (
          <div className="mb-4">
            <Alert tone="danger">{error}</Alert>
          </div>
        ) : null}

        {/*
          Stated once, prominently: the imported classification is derived,
          not authoritative. Showing it without this caveat would let derived
          data pass for fact.
        */}
        {unconfirmed > 0 ? (
          <div className="mb-4">
            <Alert tone="warning">
              {unconfirmed} — {t.accounting.needsConfirmationHint}
            </Alert>
          </div>
        ) : null}

        {rows.length === 0 ? (
          <EmptyState
            title={t.accounting.emptyChartTitle}
            body={t.accounting.emptyChartBody}
            action={
              mayManage ? (
                <Button variant="primary" onClick={() => setSeedOpen(true)}>
                  {t.accounting.seedChart}
                </Button>
              ) : undefined
            }
          />
        ) : (
          <Table>
            <thead>
              <tr>
                <Th>{t.accounting.code}</Th>
                <Th>{t.accounting.name}</Th>
                <Th>{t.accounting.level}</Th>
                <Th>{t.accounting.element}</Th>
                <Th>{t.accounting.status}</Th>
              </tr>
            </thead>
            <tbody>
              {rows.slice(0, 400).map((account) => (
                <AccountRow key={account.id} account={account} />
              ))}
            </tbody>
          </Table>
        )}
      </Card>

      <ConfirmDialog
        open={seedOpen}
        onClose={() => setSeedOpen(false)}
        onConfirm={() => seed.mutate()}
        title={t.accounting.seedChartTitle}
        body={t.accounting.seedChartBody}
        confirmLabel={t.accounting.seedChart}
        cancelLabel={t.common.cancel}
        closeLabel={t.common.close}
        loading={seed.isPending}
      />
    </>
  )
}

function AccountRow({ account }: { account: Account }) {
  const { t } = useI18n()
  // Indent by hierarchy level so the structure is readable at a glance.
  const depth = { PART: 0, SECTION: 1, GROUP: 2, ACCOUNT: 3, SUBACCOUNT: 4, SYNTHETIC: 4 }[
    account.level
  ]
  return (
    <tr>
      <Td>
        <span
          className="font-mono text-xs"
          style={{ paddingInlineStart: `${depth * 0.75}rem` }}
        >
          {account.code}
        </span>
      </Td>
      <Td>
        <span className={account.is_postable ? '' : 'font-semibold'}>
          {account.name}
        </span>
      </Td>
      <Td>
        {account.is_postable ? (
          <Badge tone="brand">{t.accounting.postable}</Badge>
        ) : (
          <Badge>{t.accounting.structural}</Badge>
        )}
      </Td>
      <Td>
        {account.element ? (
          <span className="text-xs text-[var(--text-secondary)]">
            {account.element}
          </span>
        ) : (
          <span className="text-xs text-[var(--text-muted)]">—</span>
        )}
      </Td>
      <Td>
        {account.needs_confirmation ? (
          <span title={t.accounting.needsConfirmationHint}>
            <Badge tone="warning">{t.accounting.needsConfirmation}</Badge>
          </span>
        ) : (
          <Badge tone="success">{t.common.active}</Badge>
        )}
      </Td>
    </tr>
  )
}
