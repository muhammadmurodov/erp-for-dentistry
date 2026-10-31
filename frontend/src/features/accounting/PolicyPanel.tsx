import { useEffect, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useI18n } from '@/i18n'
import { useAuth } from '@/features/auth/AuthProvider'
import { api } from '@/lib/api'
import { useAccounts, usePolicies, usePolicyRules } from '@/lib/queries'
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
  Select,
  Table,
  Td,
  Th,
} from '@/components/ui'
import type { AccountingPolicy, PolicyRule, PolicyStatus } from '@/lib/types'

function StatusBadge({ status }: { status: PolicyStatus }) {
  const { t } = useI18n()
  if (status === 'ACTIVE') return <Badge tone="success">{t.accounting.policyActive}</Badge>
  if (status === 'DRAFT') return <Badge tone="warning">{t.accounting.policyDraft}</Badge>
  return <Badge>{t.accounting.policySuperseded}</Badge>
}

/** Today, as the date input wants it. */
function today(): string {
  return new Date().toISOString().slice(0, 10)
}

export function PolicyPanel() {
  const { t, formatDate } = useI18n()
  const { can } = useAuth()
  const queryClient = useQueryClient()
  const mayManage = can('accounting.policy.manage')

  const policies = usePolicies(true)
  const [selected, setSelected] = useState<string | null>(null)
  const [createOpen, setCreateOpen] = useState(false)
  const [seedOpen, setSeedOpen] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const rows = policies.data ?? []
  // A selection is only honoured while the version still exists: discarding
  // a draft would otherwise leave the detail panel asking the server for a
  // row that has just been deleted.
  const stillThere = selected !== null && rows.some((p) => p.id === selected)
  // Otherwise land on the version in force, which is the one an accountant
  // means when they ask what the policy says.
  const active =
    (stillThere ? selected : null) ??
    rows.find((p) => p.status === 'ACTIVE')?.id ??
    rows[0]?.id ??
    null

  const invalidate = () =>
    void queryClient.invalidateQueries({ queryKey: ['accounting'] })

  if (policies.isLoading) return <LoadingState label={t.common.loading} />
  if (policies.isError) {
    return (
      <ErrorState
        title={t.errors.loadFailedTitle}
        message={describeError(policies.error, t)}
        requestId={errorRequestId(policies.error)}
        requestIdLabel={t.common.requestId}
        onRetry={() => void policies.refetch()}
        retryLabel={t.common.retry}
      />
    )
  }

  return (
    <>
      <div className="mb-4">
        <Alert tone="info">{t.accounting.policyIntro}</Alert>
      </div>
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

      {rows.length === 0 ? (
        <Card>
          <EmptyState
            title={t.accounting.emptyPolicies}
            body={mayManage ? t.accounting.emptyPoliciesBody : t.moduleStatus.noAccess}
            action={
              mayManage ? (
                <Button variant="primary" onClick={() => setSeedOpen(true)}>
                  {t.accounting.seedDefault}
                </Button>
              ) : undefined
            }
          />
        </Card>
      ) : (
        <div className="grid gap-4 xl:grid-cols-[22rem_minmax(0,1fr)]">
          <Card>
            <CardHeader
              title={t.accounting.policies}
              actions={
                mayManage ? (
                  <>
                    <Button size="sm" onClick={() => setSeedOpen(true)}>
                      {t.accounting.seedDefault}
                    </Button>
                    <Button size="sm" variant="primary" onClick={() => setCreateOpen(true)}>
                      {t.accounting.newPolicy}
                    </Button>
                  </>
                ) : undefined
              }
            />
            <Table>
              <thead>
                <tr>
                  <Th>{t.accounting.policyName}</Th>
                  <Th>{t.accounting.effectiveFrom}</Th>
                  <Th>{t.accounting.policyStatus}</Th>
                </tr>
              </thead>
              <tbody>
                {rows.map((policy: AccountingPolicy) => (
                  <tr
                    key={policy.id}
                    onClick={() => setSelected(policy.id)}
                    className={
                      'cursor-pointer ' +
                      (policy.id === active
                        ? 'bg-brand-50'
                        : 'hover:bg-[var(--surface-sunken)]')
                    }
                  >
                    <Td>
                      {policy.name}
                      <div className="text-[var(--text-secondary)]">
                        v{policy.version}
                      </div>
                    </Td>
                    <Td>{formatDate(policy.effective_from)}</Td>
                    <Td>
                      <StatusBadge status={policy.status} />
                    </Td>
                  </tr>
                ))}
              </tbody>
            </Table>
          </Card>

          {active ? (
            <PolicyDetail
              policy={rows.find((p) => p.id === active) ?? null}
              onChanged={(message) => {
                setError(null)
                setNotice(message)
                invalidate()
              }}
              onError={(message) => {
                setNotice(null)
                setError(message)
              }}
            />
          ) : (
            <Card>
              <EmptyState title={t.accounting.selectPolicy} />
            </Card>
          )}
        </div>
      )}

      <SeedDialog
        open={seedOpen}
        onClose={() => setSeedOpen(false)}
        onDone={(policy) => {
          setSeedOpen(false)
          setError(null)
          setSelected(policy.id)
          setNotice(t.accounting.seedDefaultDone)
          invalidate()
        }}
        onError={(message) => {
          setSeedOpen(false)
          setError(message)
        }}
      />

      <CreateDialog
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onDone={(policy) => {
          setCreateOpen(false)
          setError(null)
          setSelected(policy.id)
          setNotice(t.accounting.policyCreated)
          invalidate()
        }}
        onError={(message) => {
          setCreateOpen(false)
          setError(message)
        }}
      />
    </>
  )
}

function PolicyDetail({
  policy,
  onChanged,
  onError,
}: {
  policy: AccountingPolicy | null
  onChanged: (message: string) => void
  onError: (message: string) => void
}) {
  const { t, formatDate, formatDateTime } = useI18n()
  const { can } = useAuth()
  const rules = usePolicyRules(policy?.id ?? null)
  const [editing, setEditing] = useState<PolicyRule | null>(null)
  const [adding, setAdding] = useState(false)

  const mayManage = can('accounting.policy.manage')
  const isDraft = policy?.status === 'DRAFT'

  const [discarding, setDiscarding] = useState(false)

  const discard = useMutation({
    mutationFn: () => api.delete(`/accounting/policies/${policy?.id}`),
    onSuccess: () => {
      setDiscarding(false)
      onChanged(t.accounting.discarded)
    },
    onError: (err) => {
      setDiscarding(false)
      onError(describeError(err, t))
    },
  })

  const activate = useMutation({
    mutationFn: () =>
      api.post<AccountingPolicy>(`/accounting/policies/${policy?.id}/activate`),
    onSuccess: () => onChanged(t.accounting.activated),
    onError: (err) => onError(describeError(err, t)),
  })

  if (!policy) return null

  return (
    <>
      <Card>
        <CardHeader
          title={`${policy.name} · v${policy.version}`}
          description={policy.notes ?? undefined}
          actions={
            mayManage && isDraft ? (
              <>
                <Button size="sm" onClick={() => setAdding(true)}>
                  {t.accounting.addRule}
                </Button>
                <Button size="sm" onClick={() => setDiscarding(true)}>
                  {t.accounting.discardDraft}
                </Button>
                <Button
                  size="sm"
                  variant="primary"
                  loading={activate.isPending}
                  onClick={() => activate.mutate()}
                >
                  {t.accounting.activatePolicy}
                </Button>
              </>
            ) : undefined
          }
        />

        <dl className="mb-4 grid grid-cols-2 gap-x-6 gap-y-1 text-sm">
          <dt className="text-[var(--text-secondary)]">{t.accounting.policyStatus}</dt>
          <dd className="text-right">
            <StatusBadge status={policy.status} />
          </dd>
          <dt className="text-[var(--text-secondary)]">{t.accounting.effectiveFrom}</dt>
          <dd className="text-right">{formatDate(policy.effective_from)}</dd>
          <dt className="text-[var(--text-secondary)]">{t.accounting.effectiveTo}</dt>
          <dd className="text-right">
            {policy.effective_to ? formatDate(policy.effective_to) : '—'}
          </dd>
          <dt className="text-[var(--text-secondary)]">{t.accounting.activatedAt}</dt>
          <dd className="text-right">
            {policy.activated_at ? formatDateTime(policy.activated_at) : '—'}
          </dd>
          <dt className="text-[var(--text-secondary)]">{t.accounting.ruleCount}</dt>
          <dd className="tabular text-right">{policy.rule_count}</dd>
        </dl>

        {isDraft ? (
          <div className="mb-4">
            <Alert tone="warning">{t.accounting.activateHint}</Alert>
          </div>
        ) : mayManage ? (
          <div className="mb-4">
            <Alert tone="info">{t.accounting.draftOnly}</Alert>
          </div>
        ) : null}

        {rules.isLoading ? (
          <LoadingState label={t.common.loading} />
        ) : (rules.data ?? []).length === 0 ? (
          <EmptyState title={t.accounting.emptyRules} />
        ) : (
          <div className="max-h-[32rem] overflow-y-auto">
            <Table>
              <thead>
                <tr>
                  <Th>{t.accounting.operation}</Th>
                  <Th>{t.accounting.debitAccount}</Th>
                  <Th>{t.accounting.creditAccount}</Th>
                  {mayManage && isDraft ? <Th>{t.common.actions}</Th> : null}
                </tr>
              </thead>
              <tbody>
                {(rules.data ?? []).map((rule) => (
                  <tr key={rule.id}>
                    <Td>
                      <span className="font-medium">{rule.operation_code}</span>
                      <div className="text-[var(--text-secondary)]">{rule.description}</div>
                      {rule.required_documents ? (
                        <div className="mt-1 text-xs text-[var(--text-secondary)]">
                          {rule.required_documents}
                        </div>
                      ) : null}
                    </Td>
                    <Td className="tabular">{rule.debit_account_code}</Td>
                    <Td className="tabular">{rule.credit_account_code}</Td>
                    {mayManage && isDraft ? (
                      <Td>
                        <Button size="sm" onClick={() => setEditing(rule)}>
                          {t.accounting.editRule}
                        </Button>
                      </Td>
                    ) : null}
                  </tr>
                ))}
              </tbody>
            </Table>
          </div>
        )}
      </Card>

      <ConfirmDialog
        open={discarding}
        title={t.accounting.discardTitle}
        body={t.accounting.discardHint}
        confirmLabel={t.accounting.discardDraft}
        cancelLabel={t.common.cancel}
        destructive
        closeLabel={t.common.close}
        loading={discard.isPending}
        onConfirm={() => discard.mutate()}
        onClose={() => setDiscarding(false)}
      />

      <RuleDialog
        open={adding || editing !== null}
        policyId={policy.id}
        rule={editing}
        onClose={() => {
          setAdding(false)
          setEditing(null)
        }}
        onDone={() => {
          setAdding(false)
          setEditing(null)
          onChanged(t.accounting.ruleSaved)
        }}
        onError={(message) => {
          setAdding(false)
          setEditing(null)
          onError(message)
        }}
      />
    </>
  )
}

function RuleDialog({
  open,
  policyId,
  rule,
  onClose,
  onDone,
  onError,
}: {
  open: boolean
  policyId: string
  rule: PolicyRule | null
  onClose: () => void
  onDone: () => void
  onError: (message: string) => void
}) {
  const { t } = useI18n()
  const accounts = useAccounts(open, { postable_only: true })
  const [operation, setOperation] = useState('')
  const [description, setDescription] = useState('')
  const [debit, setDebit] = useState('')
  const [credit, setCredit] = useState('')

  // Loaded when the dialog opens, not once: `rule` changes between openings.
  useEffect(() => {
    if (!open) return
    setOperation(rule?.operation_code ?? '')
    setDescription(rule?.description ?? '')
    setDebit(rule?.debit_account_code ?? '')
    setCredit(rule?.credit_account_code ?? '')
  }, [open, rule])

  const act = useMutation({
    mutationFn: () =>
      api.put(`/accounting/policies/${policyId}/rules`, {
        operation_code: operation.trim(),
        description: description.trim(),
        debit_account_code: debit,
        credit_account_code: credit,
      }),
    onSuccess: onDone,
    onError: (err) => onError(describeError(err, t)),
  })

  const options = accounts.data ?? []

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={rule ? `${t.accounting.editRule} — ${rule.operation_code}` : t.accounting.addRule}
      closeLabel={t.common.close}
      footer={
        <>
          <Button onClick={onClose} disabled={act.isPending}>
            {t.common.cancel}
          </Button>
          <Button
            variant="primary"
            loading={act.isPending}
            disabled={!operation.trim() || !description.trim() || !debit || !credit}
            onClick={() => act.mutate()}
          >
            {t.common.save}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <Field label={t.accounting.operation} htmlFor="rule-operation" required>
          <Input
            id="rule-operation"
            value={operation}
            // The code identifies the rule, so changing it on an existing one
            // would create a second rule rather than edit this one.
            disabled={rule !== null}
            onChange={(event) => setOperation(event.target.value)}
          />
        </Field>
        <Field label={t.accounting.ruleDescription} htmlFor="rule-description" required>
          <Input
            id="rule-description"
            value={description}
            onChange={(event) => setDescription(event.target.value)}
          />
        </Field>
        <Field label={t.accounting.debitAccount} htmlFor="rule-debit" required>
          <Select
            id="rule-debit"
            value={debit}
            onChange={(event) => setDebit(event.target.value)}
          >
            <option value="">—</option>
            {options.map((account) => (
              <option key={account.id} value={account.code}>
                {account.code} · {account.name}
              </option>
            ))}
          </Select>
        </Field>
        <Field label={t.accounting.creditAccount} htmlFor="rule-credit" required>
          <Select
            id="rule-credit"
            value={credit}
            onChange={(event) => setCredit(event.target.value)}
          >
            <option value="">—</option>
            {options.map((account) => (
              <option key={account.id} value={account.code}>
                {account.code} · {account.name}
              </option>
            ))}
          </Select>
        </Field>
        {rule?.required_documents ? (
          <Alert tone="info">
            {t.accounting.ruleSource}: {rule.required_documents}
          </Alert>
        ) : null}
      </div>
    </Dialog>
  )
}

function SeedDialog({
  open,
  onClose,
  onDone,
  onError,
}: {
  open: boolean
  onClose: () => void
  onDone: (policy: AccountingPolicy) => void
  onError: (message: string) => void
}) {
  const { t } = useI18n()
  const [from, setFrom] = useState(today())

  const act = useMutation({
    mutationFn: () =>
      api.post<AccountingPolicy>(
        '/accounting/policies/seed-default',
        undefined,
        { query: { effective_from: from } },
      ),
    onSuccess: onDone,
    onError: (err) => onError(describeError(err, t)),
  })

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={t.accounting.seedDefault}
      closeLabel={t.common.close}
      footer={
        <>
          <Button onClick={onClose} disabled={act.isPending}>
            {t.common.cancel}
          </Button>
          <Button
            variant="primary"
            loading={act.isPending}
            disabled={!from}
            onClick={() => act.mutate()}
          >
            {t.common.create}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <Alert tone="info">{t.accounting.seedDefaultHint}</Alert>
        <Alert tone="warning">{t.accounting.unreviewed}</Alert>
        <Field label={t.accounting.effectiveFrom} htmlFor="seed-from" required>
          <Input
            id="seed-from"
            type="date"
            value={from}
            onChange={(event) => setFrom(event.target.value)}
          />
        </Field>
      </div>
    </Dialog>
  )
}

function CreateDialog({
  open,
  onClose,
  onDone,
  onError,
}: {
  open: boolean
  onClose: () => void
  onDone: (policy: AccountingPolicy) => void
  onError: (message: string) => void
}) {
  const { t } = useI18n()
  const [name, setName] = useState('')
  const [from, setFrom] = useState(today())

  const act = useMutation({
    mutationFn: () =>
      api.post<AccountingPolicy>('/accounting/policies', {
        name: name.trim(),
        effective_from: from,
      }),
    onSuccess: (policy) => {
      setName('')
      onDone(policy)
    },
    onError: (err) => onError(describeError(err, t)),
  })

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={t.accounting.newPolicy}
      closeLabel={t.common.close}
      footer={
        <>
          <Button onClick={onClose} disabled={act.isPending}>
            {t.common.cancel}
          </Button>
          <Button
            variant="primary"
            loading={act.isPending}
            disabled={!name.trim() || !from}
            onClick={() => act.mutate()}
          >
            {t.common.create}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <Field label={t.accounting.policyName} htmlFor="policy-name" required>
          <Input
            id="policy-name"
            value={name}
            onChange={(event) => setName(event.target.value)}
          />
        </Field>
        <Field label={t.accounting.effectiveFrom} htmlFor="policy-from" required>
          <Input
            id="policy-from"
            type="date"
            value={from}
            onChange={(event) => setFrom(event.target.value)}
          />
        </Field>
      </div>
    </Dialog>
  )
}
