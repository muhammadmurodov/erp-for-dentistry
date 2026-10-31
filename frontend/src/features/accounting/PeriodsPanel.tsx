import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useI18n } from '@/i18n'
import { useAuth } from '@/features/auth/AuthProvider'
import { api } from '@/lib/api'
import { useFiscalYears, usePeriods } from '@/lib/queries'
import { describeError, errorRequestId } from '@/lib/errorMessage'
import {
  Alert,
  Badge,
  Button,
  Card,
  CardHeader,
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
import type { AccountingPeriod, FiscalYear } from '@/lib/types'

export function PeriodsPanel() {
  const { t, formatDate } = useI18n()
  const { can } = useAuth()
  const queryClient = useQueryClient()

  const mayRead = can('organization.period.read')
  const mayManage = can('organization.period.manage')
  const years = useFiscalYears(mayRead)
  const periods = usePeriods(mayRead)

  const [yearOpen, setYearOpen] = useState(false)
  const [closeTarget, setCloseTarget] = useState<AccountingPeriod | null>(null)
  const [reopenTarget, setReopenTarget] = useState<AccountingPeriod | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const invalidate = () =>
    void queryClient.invalidateQueries({ queryKey: ['accounting'] })

  const close = useMutation({
    mutationFn: (id: string) =>
      api.post<AccountingPeriod>(`/accounting/periods/${id}/close`),
    onSuccess: () => {
      setCloseTarget(null)
      setError(null)
      setNotice(t.accounting.periodClosed)
      invalidate()
    },
    onError: (err) => {
      setCloseTarget(null)
      setError(describeError(err, t))
    },
  })

  if (!mayRead) return <Alert tone="warning">{t.moduleStatus.noAccess}</Alert>
  if (years.isLoading || periods.isLoading) {
    return <LoadingState label={t.common.loading} />
  }
  if (years.isError) {
    return (
      <ErrorState
        title={t.errors.loadFailedTitle}
        message={describeError(years.error, t)}
        requestId={errorRequestId(years.error)}
        requestIdLabel={t.common.requestId}
        onRetry={() => void years.refetch()}
        retryLabel={t.common.retry}
      />
    )
  }

  const yearRows = years.data ?? []
  const periodRows = periods.data ?? []

  return (
    <>
      <Card>
        <CardHeader
          title={t.accounting.periodsTab}
          actions={
            mayManage ? (
              <Button variant="primary" size="sm" onClick={() => setYearOpen(true)}>
                {t.accounting.newFiscalYear}
              </Button>
            ) : undefined
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

        {yearRows.length === 0 ? (
          <EmptyState
            title={t.accounting.noPeriodsTitle}
            body={t.accounting.noPeriodsBody}
            action={
              mayManage ? (
                <Button variant="primary" onClick={() => setYearOpen(true)}>
                  {t.accounting.newFiscalYear}
                </Button>
              ) : undefined
            }
          />
        ) : (
          <div className="flex flex-col gap-6">
            {yearRows.map((year) => (
              <div key={year.id}>
                <h3 className="mb-2 flex items-center gap-2 text-sm font-semibold">
                  {t.accounting.fiscalYear} {year.code}
                  <span className="text-xs font-normal text-[var(--text-secondary)]">
                    {formatDate(year.starts_on)} — {formatDate(year.ends_on)}
                  </span>
                </h3>
                <Table>
                  <thead>
                    <tr>
                      <Th>{t.accounting.code}</Th>
                      <Th>{t.accounting.startsOn}</Th>
                      <Th>{t.accounting.endsOn}</Th>
                      <Th>{t.accounting.status}</Th>
                      {mayManage ? <Th>{t.common.actions}</Th> : null}
                    </tr>
                  </thead>
                  <tbody>
                    {periodRows
                      .filter((p) => p.fiscal_year_id === year.id)
                      .map((period) => (
                        <tr key={period.id}>
                          <Td>
                            <span className="font-mono text-xs">{period.code}</span>
                          </Td>
                          <Td>{formatDate(period.starts_on)}</Td>
                          <Td>{formatDate(period.ends_on)}</Td>
                          <Td>
                            {period.status === 'OPEN' ? (
                              <Badge tone="success">{t.accounting.open}</Badge>
                            ) : (
                              <Badge tone="neutral">{t.accounting.closed}</Badge>
                            )}
                            {period.reopened_reason ? (
                              <span
                                className="ml-2 text-xs text-[var(--text-muted)]"
                                title={period.reopened_reason}
                              >
                                ↻
                              </span>
                            ) : null}
                          </Td>
                          {mayManage ? (
                            <Td>
                              {period.status === 'OPEN' ? (
                                <Button
                                  size="sm"
                                  variant="ghost"
                                  onClick={() => {
                                    setError(null)
                                    setCloseTarget(period)
                                  }}
                                >
                                  {t.accounting.closePeriod}
                                </Button>
                              ) : (
                                <Button
                                  size="sm"
                                  variant="ghost"
                                  onClick={() => {
                                    setError(null)
                                    setReopenTarget(period)
                                  }}
                                >
                                  {t.accounting.reopenPeriod}
                                </Button>
                              )}
                            </Td>
                          ) : null}
                        </tr>
                      ))}
                  </tbody>
                </Table>
              </div>
            ))}
          </div>
        )}
      </Card>

      <FiscalYearDialog
        open={yearOpen}
        onClose={() => setYearOpen(false)}
        onCreated={() => {
          setYearOpen(false)
          setError(null)
          invalidate()
        }}
        onError={(message) => {
          setYearOpen(false)
          setError(message)
        }}
      />

      <ConfirmDialog
        open={closeTarget !== null}
        onClose={() => setCloseTarget(null)}
        onConfirm={() => closeTarget && close.mutate(closeTarget.id)}
        title={`${t.accounting.closePeriodTitle}: ${closeTarget?.code ?? ''}`}
        body={t.accounting.closePeriodBody}
        confirmLabel={t.accounting.closePeriod}
        cancelLabel={t.common.cancel}
        closeLabel={t.common.close}
        loading={close.isPending}
      />

      <ReopenDialog
        period={reopenTarget}
        onClose={() => setReopenTarget(null)}
        onDone={() => {
          setReopenTarget(null)
          setNotice(t.accounting.periodReopened)
          invalidate()
        }}
        onError={(message) => {
          setReopenTarget(null)
          setError(message)
        }}
      />
    </>
  )
}

function FiscalYearDialog({
  open,
  onClose,
  onCreated,
  onError,
}: {
  open: boolean
  onClose: () => void
  onCreated: () => void
  onError: (message: string) => void
}) {
  const { t } = useI18n()
  const thisYear = new Date().getFullYear()
  const [code, setCode] = useState(String(thisYear))
  const [startsOn, setStartsOn] = useState(`${thisYear}-01-01`)
  const [endsOn, setEndsOn] = useState(`${thisYear}-12-31`)

  const create = useMutation({
    mutationFn: () =>
      api.post<FiscalYear>('/accounting/fiscal-years', {
        code,
        starts_on: startsOn,
        ends_on: endsOn,
        create_monthly_periods: true,
      }),
    onSuccess: onCreated,
    onError: (err) => onError(describeError(err, t)),
  })

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={t.accounting.newFiscalYear}
      closeLabel={t.common.close}
      footer={
        <>
          <Button onClick={onClose} disabled={create.isPending}>
            {t.common.cancel}
          </Button>
          <Button
            variant="primary"
            loading={create.isPending}
            onClick={() => create.mutate()}
          >
            {t.common.create}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <Field label={t.accounting.code} htmlFor="fy-code" required>
          <Input
            id="fy-code"
            value={code}
            onChange={(event) => setCode(event.target.value)}
          />
        </Field>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label={t.accounting.startsOn} htmlFor="fy-start" required>
            <Input
              id="fy-start"
              type="date"
              value={startsOn}
              onChange={(event) => setStartsOn(event.target.value)}
            />
          </Field>
          <Field label={t.accounting.endsOn} htmlFor="fy-end" required>
            <Input
              id="fy-end"
              type="date"
              value={endsOn}
              onChange={(event) => setEndsOn(event.target.value)}
            />
          </Field>
        </div>
      </div>
    </Dialog>
  )
}

function ReopenDialog({
  period,
  onClose,
  onDone,
  onError,
}: {
  period: AccountingPeriod | null
  onClose: () => void
  onDone: () => void
  onError: (message: string) => void
}) {
  const { t } = useI18n()
  const [reason, setReason] = useState('')

  const reopen = useMutation({
    mutationFn: () =>
      api.post<AccountingPeriod>(`/accounting/periods/${period?.id}/reopen`, {
        reason,
      }),
    onSuccess: () => {
      setReason('')
      onDone()
    },
    onError: (err) => onError(describeError(err, t)),
  })

  return (
    <Dialog
      open={period !== null}
      onClose={onClose}
      title={`${t.accounting.reopenPeriodTitle}: ${period?.code ?? ''}`}
      closeLabel={t.common.close}
      footer={
        <>
          <Button onClick={onClose} disabled={reopen.isPending}>
            {t.common.cancel}
          </Button>
          <Button
            variant="primary"
            loading={reopen.isPending}
            disabled={!reason.trim()}
            onClick={() => reopen.mutate()}
          >
            {t.accounting.reopenPeriod}
          </Button>
        </>
      }
    >
      <Field
        label={t.accounting.reason}
        htmlFor="reopen-reason"
        required
        hint={t.accounting.reopenReasonHint}
      >
        <Input
          id="reopen-reason"
          value={reason}
          onChange={(event) => setReason(event.target.value)}
        />
      </Field>
    </Dialog>
  )
}
