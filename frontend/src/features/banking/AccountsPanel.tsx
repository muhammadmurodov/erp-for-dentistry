import { useEffect, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useI18n } from '@/i18n'
import { useAuth } from '@/features/auth/AuthProvider'
import { api } from '@/lib/api'
import { useAccounts, useBankAccounts, useColumnMappingDefaults } from '@/lib/queries'
import { describeError, errorRequestId } from '@/lib/errorMessage'
import {
  Alert,
  Badge,
  Button,
  Card,
  CardHeader,
  Checkbox,
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
import type { BankAccount } from '@/lib/types'

export function AccountsPanel() {
  const { t } = useI18n()
  const { can } = useAuth()
  const queryClient = useQueryClient()
  const mayManage = can('banking.account.manage')

  const [includeInactive, setIncludeInactive] = useState(false)
  const [createOpen, setCreateOpen] = useState(false)
  const [mappingFor, setMappingFor] = useState<BankAccount | null>(null)
  const [deactivating, setDeactivating] = useState<BankAccount | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const accounts = useBankAccounts(true, includeInactive)
  const invalidate = () =>
    void queryClient.invalidateQueries({ queryKey: ['banking'] })

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

  return (
    <>
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

      <Card>
        <CardHeader
          title={t.banking.accounts}
          description={t.banking.pairHint}
          actions={
            <>
              <Checkbox
                label={t.banking.showInactive}
                checked={includeInactive}
                onChange={(event) => setIncludeInactive(event.target.checked)}
              />
              {mayManage ? (
                <Button size="sm" variant="primary" onClick={() => setCreateOpen(true)}>
                  {t.banking.newAccount}
                </Button>
              ) : null}
            </>
          }
        />

        {rows.length === 0 ? (
          <EmptyState
            title={t.banking.emptyAccounts}
            body={mayManage ? t.banking.addFirstAccount : t.moduleStatus.noAccess}
            action={
              mayManage ? (
                <Button variant="primary" onClick={() => setCreateOpen(true)}>
                  {t.banking.newAccount}
                </Button>
              ) : undefined
            }
          />
        ) : (
          <Table>
            <thead>
              <tr>
                <Th>{t.banking.code}</Th>
                <Th>{t.banking.name}</Th>
                <Th>{t.banking.bankName}</Th>
                <Th>{t.banking.mfo}</Th>
                <Th>{t.banking.accountNumber}</Th>
                <Th>{t.banking.columnMapping}</Th>
                <Th>{t.common.actions}</Th>
              </tr>
            </thead>
            <tbody>
              {rows.map((account) => (
                <tr key={account.id}>
                  <Td>{account.code}</Td>
                  <Td>
                    {account.name}
                    {account.is_active ? null : (
                      <>
                        {' '}
                        <Badge>{t.banking.inactive}</Badge>
                      </>
                    )}
                  </Td>
                  <Td>{account.bank_name ?? '—'}</Td>
                  <Td>{account.mfo ?? '—'}</Td>
                  <Td className="tabular">{account.account_number_masked ?? '—'}</Td>
                  <Td>
                    {account.has_column_mapping ? (
                      <Badge tone="brand">{t.banking.mappingConfigured}</Badge>
                    ) : (
                      <span className="text-[var(--text-secondary)]">
                        {t.banking.mappingDefault}
                      </span>
                    )}
                  </Td>
                  <Td>
                    {mayManage ? (
                      <div className="flex flex-wrap gap-2">
                        <Button size="sm" onClick={() => setMappingFor(account)}>
                          {t.banking.editMapping}
                        </Button>
                        {account.is_active ? (
                          <Button size="sm" onClick={() => setDeactivating(account)}>
                            {t.banking.deactivate}
                          </Button>
                        ) : null}
                      </div>
                    ) : (
                      '—'
                    )}
                  </Td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </Card>

      <CreateAccountDialog
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onDone={() => {
          setCreateOpen(false)
          setError(null)
          setNotice(t.banking.accountCreated)
          invalidate()
        }}
        onError={(message) => {
          setCreateOpen(false)
          setError(message)
        }}
      />

      <ColumnMappingDialog
        account={mappingFor}
        onClose={() => setMappingFor(null)}
        onDone={() => {
          setMappingFor(null)
          setError(null)
          setNotice(t.banking.mappingSaved)
          invalidate()
        }}
        onError={(message) => {
          setMappingFor(null)
          setError(message)
        }}
      />

      <DeactivateDialog
        account={deactivating}
        onClose={() => setDeactivating(null)}
        onDone={() => {
          setDeactivating(null)
          setError(null)
          setNotice(t.banking.deactivated)
          invalidate()
        }}
        onError={(message) => {
          setDeactivating(null)
          setError(message)
        }}
      />
    </>
  )
}

function CreateAccountDialog({
  open,
  onClose,
  onDone,
  onError,
}: {
  open: boolean
  onClose: () => void
  onDone: () => void
  onError: (message: string) => void
}) {
  const { t } = useI18n()
  const [code, setCode] = useState('')
  const [name, setName] = useState('')
  const [bankName, setBankName] = useState('')
  const [mfo, setMfo] = useState('')
  const [number, setNumber] = useState('')
  const [accountCode, setAccountCode] = useState('5110.01')

  // Only postable accounts: a statutory group heading cannot hold a balance,
  // and the backend refuses one, so it is not offered here either. The whole
  // postable chart is listed rather than a guessed subset — each option is
  // prefixed with its code, which is what a native select's type-ahead
  // matches on, so 5110 is four keystrokes away.
  const ledgerAccounts = useAccounts(open, { postable_only: true })

  const act = useMutation({
    mutationFn: () =>
      api.post<BankAccount>('/banking/accounts', {
        code,
        name,
        account_code: accountCode,
        bank_name: bankName || null,
        mfo: mfo || null,
        account_number: number || null,
      }),
    onSuccess: () => {
      setCode('')
      setName('')
      setBankName('')
      setMfo('')
      setNumber('')
      onDone()
    },
    onError: (err) => onError(describeError(err, t)),
  })

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={t.banking.newAccount}
      closeLabel={t.common.close}
      footer={
        <>
          <Button onClick={onClose} disabled={act.isPending}>
            {t.common.cancel}
          </Button>
          <Button
            variant="primary"
            loading={act.isPending}
            disabled={!code.trim() || !name.trim() || !accountCode}
            onClick={() => act.mutate()}
          >
            {t.common.save}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <Field label={t.banking.code} htmlFor="bank-code" required>
          <Input
            id="bank-code"
            value={code}
            onChange={(event) => setCode(event.target.value)}
          />
        </Field>
        <Field label={t.banking.name} htmlFor="bank-name" required>
          <Input
            id="bank-name"
            value={name}
            onChange={(event) => setName(event.target.value)}
          />
        </Field>
        <Field
          label={t.banking.ledgerAccount}
          htmlFor="bank-ledger"
          hint={t.banking.pairHint}
          required
        >
          <Select
            id="bank-ledger"
            value={accountCode}
            onChange={(event) => setAccountCode(event.target.value)}
          >
            {(ledgerAccounts.data ?? []).map((account) => (
              <option key={account.id} value={account.code}>
                {account.code} · {account.name}
              </option>
            ))}
          </Select>
        </Field>
        <Field label={t.banking.bankName} htmlFor="bank-bank">
          <Input
            id="bank-bank"
            value={bankName}
            onChange={(event) => setBankName(event.target.value)}
          />
        </Field>
        <Field label={t.banking.mfo} htmlFor="bank-mfo">
          <Input
            id="bank-mfo"
            inputMode="numeric"
            className="tabular"
            value={mfo}
            onChange={(event) => setMfo(event.target.value)}
          />
        </Field>
        <Field
          label={t.banking.accountNumber}
          htmlFor="bank-number"
          hint={t.banking.numberHint}
        >
          <Input
            id="bank-number"
            inputMode="numeric"
            className="tabular"
            value={number}
            onChange={(event) => setNumber(event.target.value)}
          />
        </Field>
      </div>
    </Dialog>
  )
}

function ColumnMappingDialog({
  account,
  onClose,
  onDone,
  onError,
}: {
  account: BankAccount | null
  onClose: () => void
  onDone: () => void
  onError: (message: string) => void
}) {
  const { t } = useI18n()
  const defaults = useColumnMappingDefaults(Boolean(account))
  const [mapping, setMapping] = useState<Record<string, string>>({})

  // A fresh dialog each time: the previous account's headings must not
  // appear against this one's.
  useEffect(() => {
    setMapping({})
  }, [account?.id])

  const act = useMutation({
    mutationFn: () => {
      const filled = Object.fromEntries(
        Object.entries(mapping).filter(([, value]) => value.trim()),
      )
      return api.put<BankAccount>(
        `/banking/accounts/${account?.id}/column-mapping`,
        { column_mapping: Object.keys(filled).length > 0 ? filled : null },
      )
    },
    onSuccess: onDone,
    onError: (err) => onError(describeError(err, t)),
  })

  const fields = Object.entries(defaults.data?.aliases ?? {})

  return (
    <Dialog
      open={Boolean(account)}
      onClose={onClose}
      title={`${t.banking.columnMapping}${account ? ` — ${account.code}` : ''}`}
      closeLabel={t.common.close}
      size="lg"
      footer={
        <>
          <Button onClick={onClose} disabled={act.isPending}>
            {t.common.cancel}
          </Button>
          <Button variant="primary" loading={act.isPending} onClick={() => act.mutate()}>
            {t.common.save}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <Alert tone="info">{t.banking.mappingHint}</Alert>
        {defaults.isLoading ? (
          <LoadingState label={t.common.loading} />
        ) : (
          <Table>
            <thead>
              <tr>
                <Th>{t.banking.mappingField}</Th>
                <Th>{t.banking.mappingHeading}</Th>
                <Th>{t.banking.mappingRecognised}</Th>
              </tr>
            </thead>
            <tbody>
              {fields.map(([fieldName, aliases]) => (
                <tr key={fieldName}>
                  <Td>
                    <label htmlFor={`map-${fieldName}`}>{fieldName}</label>
                  </Td>
                  <Td>
                    <Input
                      id={`map-${fieldName}`}
                      value={mapping[fieldName] ?? ''}
                      onChange={(event) =>
                        setMapping((current) => ({
                          ...current,
                          [fieldName]: event.target.value,
                        }))
                      }
                    />
                  </Td>
                  <Td>
                    <span className="text-[var(--text-secondary)]">
                      {aliases.join(' · ')}
                    </span>
                  </Td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </div>
    </Dialog>
  )
}

function DeactivateDialog({
  account,
  onClose,
  onDone,
  onError,
}: {
  account: BankAccount | null
  onClose: () => void
  onDone: () => void
  onError: (message: string) => void
}) {
  const { t } = useI18n()
  const [reason, setReason] = useState('')

  const act = useMutation({
    mutationFn: () =>
      api.post<BankAccount>(`/banking/accounts/${account?.id}/deactivate`, {
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
      open={Boolean(account)}
      onClose={onClose}
      title={`${t.banking.deactivate}${account ? ` — ${account.code}` : ''}`}
      closeLabel={t.common.close}
      footer={
        <>
          <Button onClick={onClose} disabled={act.isPending}>
            {t.common.cancel}
          </Button>
          <Button
            variant="primary"
            loading={act.isPending}
            disabled={reason.trim().length < 3}
            onClick={() => act.mutate()}
          >
            {t.common.confirm}
          </Button>
        </>
      }
    >
      <Field label={t.banking.deactivateReason} htmlFor="bank-deactivate" required>
        <Input
          id="bank-deactivate"
          value={reason}
          onChange={(event) => setReason(event.target.value)}
        />
      </Field>
    </Dialog>
  )
}
