import { useMemo, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useI18n } from '@/i18n'
import { useAuth } from '@/features/auth/AuthProvider'
import { api } from '@/lib/api'
import { usePayrollRules } from '@/lib/queries'
import { describeError, errorRequestId } from '@/lib/errorMessage'
import { todayInClinic } from '@/lib/clinicDate'
import { byKindNewestFirst, describeRate, isDraft, missingKinds } from '@/lib/payroll'
import type { PayrollRule, RuleMethod, StatutoryRuleKind } from '@/lib/types'
import {
  Alert,
  Badge,
  Button,
  Card,
  CardHeader,
  Dialog,
  EmptyState,
  ErrorState,
  Field,
  Input,
  LoadingState,
  Select,
  Table,
  Td,
  Textarea,
  Th,
} from '@/components/ui'

const KINDS: readonly StatutoryRuleKind[] = [
  'INCOME_TAX',
  'INPS',
  'EMPLOYEE_PENSION',
  'SOCIAL_TAX',
] as const

/**
 * The statutory rates, as dated and sourced records rather than constants.
 *
 * Everything on this screen is evidence about a rate: when it started, what
 * authority says so, who accepted it, and what it replaced. Nothing here
 * computes with a rate — the rates are displayed and approved, and the
 * calculation happens on the server against the version in force on the
 * period's end date.
 *
 * The social tax is usually missing, and that is the honest state of the
 * clinic's knowledge rather than an omission: the workbook audit could not
 * confirm its current rate, so an accountant has to enter it. Until then
 * every run is blocked, which this screen says in as many words.
 */
export function RulesPanel() {
  const { t } = useI18n()
  const { can } = useAuth()
  const queryClient = useQueryClient()
  const [proposing, setProposing] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const rules = usePayrollRules({}, true)
  const mayManage = can('payroll.rules.manage')
  const today = todayInClinic()

  const ordered = useMemo(() => byKindNewestFirst(rules.data ?? []), [rules.data])
  const missing = useMemo(() => missingKinds(rules.data ?? [], today), [rules.data, today])

  const seed = useMutation({
    mutationFn: () => api.post<PayrollRule[]>('/payroll/rules/seed', {}),
    onSuccess: async () => {
      setError(null)
      await queryClient.invalidateQueries({ queryKey: ['payroll'] })
    },
    onError: (err) => setError(describeError(err, t)),
  })

  const approve = useMutation({
    mutationFn: (ruleId: string) => api.post(`/payroll/rules/${ruleId}/approve`, {}),
    onSuccess: async () => {
      setError(null)
      await queryClient.invalidateQueries({ queryKey: ['payroll'] })
    },
    onError: (err) => setError(describeError(err, t)),
  })

  return (
    <div className="flex flex-col gap-5">
      {missing.length > 0 ? (
        <Alert tone="warning">
          <p className="font-medium">{t.payroll.missingRuleTitle}</p>
          <p className="mt-1">{t.payroll.missingRuleBody}</p>
          <ul className="mt-2 list-disc pl-5">
            {missing.map((kind) => (
              <li key={kind}>{t.payroll[`kind${kind}`]}</li>
            ))}
          </ul>
          {missing.includes('SOCIAL_TAX') ? (
            <p className="mt-2">{t.payroll.socialTaxUnknown}</p>
          ) : null}
        </Alert>
      ) : null}

      <Card>
        <CardHeader
          title={t.payroll.rules}
          description={t.payroll.rulesSubtitle}
          actions={
            mayManage ? (
              <>
                <Button loading={seed.isPending} onClick={() => seed.mutate()}>
                  {t.payroll.seedDrafts}
                </Button>
                <Button variant="primary" onClick={() => setProposing(true)}>
                  {t.payroll.proposeRule}
                </Button>
              </>
            ) : null
          }
        />
        {mayManage ? (
          <p className="mb-4 text-xs text-[var(--text-muted)]">{t.payroll.seedDraftsHint}</p>
        ) : null}
        {error ? (
          <div className="mb-4">
            <Alert tone="danger">{error}</Alert>
          </div>
        ) : null}

        {rules.isLoading ? (
          <LoadingState label={t.common.loading} />
        ) : rules.isError || !rules.data ? (
          <ErrorState
            title={t.errors.loadFailedTitle}
            message={describeError(rules.error, t)}
            requestId={errorRequestId(rules.error)}
            requestIdLabel={t.common.requestId}
            onRetry={() => void rules.refetch()}
            retryLabel={t.common.retry}
          />
        ) : ordered.length === 0 ? (
          <EmptyState title={t.payroll.noRules} body={t.payroll.noRulesBody} />
        ) : (
          <Table>
            <thead>
              <tr>
                <Th>{t.payroll.kind}</Th>
                <Th numeric>{t.payroll.rate}</Th>
                <Th>{t.payroll.effectiveFrom}</Th>
                <Th>{t.payroll.effectiveTo}</Th>
                <Th>{t.payroll.status}</Th>
                <Th>{t.payroll.legalReference}</Th>
                {mayManage ? <Th>{t.common.actions}</Th> : null}
              </tr>
            </thead>
            <tbody>
              {ordered.map((rule) => (
                <tr key={rule.id}>
                  <Td>
                    {t.payroll[`kind${rule.kind}`]}
                    {rule.component_of ? (
                      <span className="block text-xs text-[var(--text-muted)]">
                        {`${t.payroll.componentOf} ${t.payroll[`kind${rule.component_of}`]}`}
                      </span>
                    ) : null}
                  </Td>
                  <Td numeric>{describeRate(rule) ?? '—'}</Td>
                  <Td>{rule.effective_from}</Td>
                  <Td>{rule.effective_to ?? t.payroll.openEnded}</Td>
                  <Td>
                    <Badge
                      tone={
                        rule.status === 'APPROVED'
                          ? 'success'
                          : isDraft(rule.status)
                            ? 'warning'
                            : 'neutral'
                      }
                    >
                      {t.payroll[`ruleStatus${rule.status}`]}
                    </Badge>
                    {isDraft(rule.status) ? (
                      <span className="block text-xs text-[var(--text-muted)]">
                        {t.payroll.draftCalculatesNothing}
                      </span>
                    ) : null}
                  </Td>
                  <Td>
                    <span className="text-xs">{rule.legal_reference ?? '—'}</span>
                    {rule.source_url ? (
                      <a
                        href={rule.source_url}
                        target="_blank"
                        rel="noreferrer noopener"
                        className="mt-1 block text-xs text-[var(--accent)] underline"
                      >
                        {t.payroll.source}
                      </a>
                    ) : null}
                    {rule.notes ? (
                      <span className="mt-1 block text-xs text-[var(--text-muted)]">
                        {rule.notes}
                      </span>
                    ) : null}
                  </Td>
                  {mayManage ? (
                    <Td>
                      {isDraft(rule.status) ? (
                        <Button
                          size="sm"
                          variant="primary"
                          loading={approve.isPending}
                          disabled={!rule.legal_reference}
                          onClick={() => approve.mutate(rule.id)}
                        >
                          {t.payroll.approveRule}
                        </Button>
                      ) : null}
                      {isDraft(rule.status) && !rule.legal_reference ? (
                        <span className="block text-xs text-[var(--text-muted)]">
                          {t.payroll.referenceRequired}
                        </span>
                      ) : null}
                    </Td>
                  ) : null}
                </tr>
              ))}
            </tbody>
          </Table>
        )}
        <p className="mt-4 text-xs text-[var(--text-muted)]">{t.payroll.noRateInCode}</p>
      </Card>

      {proposing ? <ProposeDialog onClose={() => setProposing(false)} /> : null}
    </div>
  )
}

/**
 * Writing down a rate. It is a draft when saved and applies to nothing until
 * somebody with authority approves it — and it cannot be approved at all
 * without the reference that proves it.
 */
function ProposeDialog({ onClose }: { onClose: () => void }) {
  const { t } = useI18n()
  const queryClient = useQueryClient()
  const [kind, setKind] = useState<StatutoryRuleKind>('SOCIAL_TAX')
  const [method, setMethod] = useState<RuleMethod>('PERCENT_OF_GROSS')
  const [rate, setRate] = useState('')
  const [fixedAmount, setFixedAmount] = useState('')
  const [effectiveFrom, setEffectiveFrom] = useState(() => `${todayInClinic().slice(0, 4)}-01-01`)
  const [effectiveTo, setEffectiveTo] = useState('')
  const [reference, setReference] = useState('')
  const [sourceUrl, setSourceUrl] = useState('')
  const [error, setError] = useState<string | null>(null)

  const percent = method === 'PERCENT_OF_GROSS'
  // A rate is kept as the typed string all the way to the server: 11.9 read
  // as a binary float is 11.899999999999999, and the backend refuses a float
  // outright for exactly that reason.
  const rateInvalid = percent
    ? !/^\d+([.,]\d{1,4})?$/.test(rate.trim())
    : !/^\d+([.,]\d{1,2})?$/.test(fixedAmount.trim())

  const save = useMutation({
    mutationFn: () =>
      api.post<PayrollRule>('/payroll/rules', {
        kind,
        method,
        rate: percent ? rate.trim().replace(',', '.') : null,
        fixed_amount: percent ? null : fixedAmount.trim().replace(',', '.'),
        effective_from: effectiveFrom,
        effective_to: effectiveTo === '' ? null : effectiveTo,
        legal_reference: reference.trim() === '' ? null : reference.trim(),
        source_url: sourceUrl.trim() === '' ? null : sourceUrl.trim(),
      }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['payroll'] })
      onClose()
    },
    onError: (err) => setError(describeError(err, t)),
  })

  return (
    <Dialog
      open
      onClose={onClose}
      title={t.payroll.proposeRule}
      closeLabel={t.common.close}
      size="lg"
      footer={
        <>
          <Button onClick={onClose} disabled={save.isPending}>
            {t.common.cancel}
          </Button>
          <Button
            variant="primary"
            loading={save.isPending}
            disabled={rateInvalid || effectiveFrom === ''}
            onClick={() => save.mutate()}
          >
            {t.common.save}
          </Button>
        </>
      }
    >
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label={t.payroll.kind} htmlFor="rule-kind" required>
          <Select
            id="rule-kind"
            value={kind}
            onChange={(event) => setKind(event.target.value as StatutoryRuleKind)}
          >
            {KINDS.map((value) => (
              <option key={value} value={value}>
                {t.payroll[`kind${value}`]}
              </option>
            ))}
          </Select>
        </Field>
        <Field label={t.payroll.method} htmlFor="rule-method">
          <Select
            id="rule-method"
            value={method}
            onChange={(event) => setMethod(event.target.value as RuleMethod)}
          >
            <option value="PERCENT_OF_GROSS">{t.payroll.percentOfGross}</option>
            <option value="FIXED_AMOUNT">{t.payroll.fixedAmount}</option>
          </Select>
        </Field>
        {percent ? (
          <Field
            label={t.payroll.rate}
            htmlFor="rule-rate"
            required
            hint={t.payroll.rateHint}
            error={rate !== '' && rateInvalid ? t.payroll.rateInvalid : undefined}
          >
            <Input
              id="rule-rate"
              inputMode="decimal"
              value={rate}
              onChange={(event) => setRate(event.target.value)}
            />
          </Field>
        ) : (
          <Field
            label={t.payroll.fixedAmount}
            htmlFor="rule-fixed"
            required
            error={fixedAmount !== '' && rateInvalid ? t.payroll.rateInvalid : undefined}
          >
            <Input
              id="rule-fixed"
              inputMode="decimal"
              value={fixedAmount}
              onChange={(event) => setFixedAmount(event.target.value)}
            />
          </Field>
        )}
        <Field label={t.payroll.effectiveFrom} htmlFor="rule-from" required>
          <Input
            id="rule-from"
            type="date"
            value={effectiveFrom}
            onChange={(event) => setEffectiveFrom(event.target.value)}
          />
        </Field>
        <Field label={t.payroll.effectiveTo} htmlFor="rule-to" hint={t.common.optional}>
          <Input
            id="rule-to"
            type="date"
            value={effectiveTo}
            onChange={(event) => setEffectiveTo(event.target.value)}
          />
        </Field>
        <div className="sm:col-span-2">
          <Field
            label={t.payroll.legalReference}
            htmlFor="rule-reference"
            hint={t.payroll.referenceRequired}
          >
            <Textarea
              id="rule-reference"
              value={reference}
              onChange={(event) => setReference(event.target.value)}
            />
          </Field>
        </div>
        <div className="sm:col-span-2">
          <Field label={t.payroll.source} htmlFor="rule-source" hint={t.common.optional}>
            <Input
              id="rule-source"
              type="url"
              value={sourceUrl}
              onChange={(event) => setSourceUrl(event.target.value)}
            />
          </Field>
        </div>
        {error ? (
          <div className="sm:col-span-2">
            <Alert tone="danger">{error}</Alert>
          </div>
        ) : null}
      </div>
    </Dialog>
  )
}
