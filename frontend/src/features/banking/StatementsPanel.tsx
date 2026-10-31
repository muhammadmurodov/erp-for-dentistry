import { useEffect, useRef, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useI18n } from '@/i18n'
import { useAuth } from '@/features/auth/AuthProvider'
import { api } from '@/lib/api'
import { useBankAccounts, useStatementImports } from '@/lib/queries'
import { describeError, errorRequestId } from '@/lib/errorMessage'
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
  Th,
} from '@/components/ui'
import type { ImportResult } from '@/lib/types'

export function StatementsPanel() {
  const { t, formatDateTime, formatDate } = useI18n()
  const { can } = useAuth()
  const queryClient = useQueryClient()
  const mayImport = can('banking.statement.import')

  const accounts = useBankAccounts(true)
  const [accountId, setAccountId] = useState('')
  const [uploadOpen, setUploadOpen] = useState(false)
  const [result, setResult] = useState<ImportResult | null>(null)
  const [error, setError] = useState<string | null>(null)

  const imports = useStatementImports(true, accountId ? { bank_account_id: accountId } : {})
  const accountRows = accounts.data ?? []
  const nameOf = (id: string) =>
    accountRows.find((account) => account.id === id)?.code ?? '—'

  if (imports.isLoading || accounts.isLoading) {
    return <LoadingState label={t.common.loading} />
  }
  if (imports.isError) {
    return (
      <ErrorState
        title={t.errors.loadFailedTitle}
        message={describeError(imports.error, t)}
        requestId={errorRequestId(imports.error)}
        requestIdLabel={t.common.requestId}
        onRetry={() => void imports.refetch()}
        retryLabel={t.common.retry}
      />
    )
  }

  const rows = imports.data ?? []

  return (
    <>
      <div className="mb-4">
        <Alert tone="info">{t.banking.importNothingPosted}</Alert>
      </div>

      {result ? (
        <div className="mb-4">
          <Alert tone={result.rows_rejected > 0 ? 'warning' : 'success'}>
            {t.banking.imported} {t.banking.rowsImported}: {result.rows_imported} ·{' '}
            {t.banking.duplicatesSkipped}: {result.duplicates_skipped} ·{' '}
            {t.banking.rowsRejected}: {result.rows_rejected}
            {result.problems.length > 0 ? (
              <ul className="mt-2 list-inside list-disc">
                {result.problems.slice(0, 10).map((problem) => (
                  <li key={problem}>{problem}</li>
                ))}
              </ul>
            ) : null}
          </Alert>
        </div>
      ) : null}
      {error ? (
        <div className="mb-4">
          <Alert tone="danger">{error}</Alert>
        </div>
      ) : null}

      <Card>
        <CardHeader
          title={t.banking.statements}
          actions={
            <>
              <Select
                aria-label={t.banking.account}
                value={accountId}
                onChange={(event) => setAccountId(event.target.value)}
              >
                <option value="">{t.banking.allAccounts}</option>
                {accountRows.map((account) => (
                  <option key={account.id} value={account.id}>
                    {account.code} · {account.name}
                  </option>
                ))}
              </Select>
              {mayImport && accountRows.length > 0 ? (
                <Button size="sm" variant="primary" onClick={() => setUploadOpen(true)}>
                  {t.banking.importStatement}
                </Button>
              ) : null}
            </>
          }
        />

        {accountRows.length === 0 ? (
          <EmptyState title={t.banking.emptyAccounts} body={t.banking.addFirstAccount} />
        ) : rows.length === 0 ? (
          <EmptyState
            title={t.banking.emptyImports}
            body={mayImport ? t.banking.fileHint : t.moduleStatus.noAccess}
          />
        ) : (
          <Table>
            <thead>
              <tr>
                <Th>{t.banking.importedAt}</Th>
                <Th>{t.banking.account}</Th>
                <Th>{t.banking.filename}</Th>
                <Th>{t.banking.period}</Th>
                <Th numeric>{t.banking.rowsRead}</Th>
                <Th numeric>{t.banking.rowsImported}</Th>
                <Th numeric>{t.banking.duplicatesSkipped}</Th>
                <Th numeric>{t.banking.rowsRejected}</Th>
              </tr>
            </thead>
            <tbody>
              {rows.map((run) => (
                <tr key={run.id}>
                  <Td>{formatDateTime(run.created_at)}</Td>
                  <Td>{nameOf(run.bank_account_id)}</Td>
                  <Td>
                    {run.filename}
                    {run.problems && run.problems.length > 0 ? (
                      <>
                        {' '}
                        <Badge tone="warning">
                          {t.banking.problems}: {run.problems.length}
                        </Badge>
                        <ul className="mt-1 list-inside list-disc text-[var(--text-secondary)]">
                          {run.problems.slice(0, 5).map((problem) => (
                            <li key={problem}>{problem}</li>
                          ))}
                        </ul>
                      </>
                    ) : null}
                  </Td>
                  <Td>
                    {run.period_from
                      ? `${formatDate(run.period_from)} — ${formatDate(run.period_to)}`
                      : '—'}
                  </Td>
                  <Td numeric>{run.rows_read}</Td>
                  <Td numeric>{run.rows_imported}</Td>
                  <Td numeric>{run.duplicates_skipped}</Td>
                  <Td numeric>{run.rows_rejected}</Td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </Card>

      <UploadDialog
        open={uploadOpen}
        accounts={accountRows.map((account) => ({
          id: account.id,
          label: `${account.code} · ${account.name}`,
        }))}
        preselected={accountId}
        onClose={() => setUploadOpen(false)}
        onDone={(imported) => {
          setUploadOpen(false)
          setError(null)
          setResult(imported)
          void queryClient.invalidateQueries({ queryKey: ['banking'] })
        }}
        onError={(message) => {
          setUploadOpen(false)
          setResult(null)
          setError(message)
        }}
      />
    </>
  )
}

function UploadDialog({
  open,
  accounts,
  preselected,
  onClose,
  onDone,
  onError,
}: {
  open: boolean
  accounts: { id: string; label: string }[]
  preselected: string
  onClose: () => void
  onDone: (result: ImportResult) => void
  onError: (message: string) => void
}) {
  const { t } = useI18n()
  const [accountId, setAccountId] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  // Chosen when the dialog opens, not once when it is first constructed:
  // the accounts arrive asynchronously, and a value frozen before they do
  // leaves the select empty.
  //
  // With more than one account and no filter to inherit, nothing is
  // preselected on purpose. Importing a statement into the wrong bank
  // account is quiet — its only symptom is a high "already held" count —
  // and a default that is merely the first account alphabetically is not a
  // good enough reason to risk it.
  useEffect(() => {
    if (!open) return
    if (preselected) setAccountId(preselected)
    else if (accounts.length === 1 && accounts[0]) setAccountId(accounts[0].id)
    else setAccountId('')
  }, [open, preselected, accounts])

  const act = useMutation({
    mutationFn: () => {
      if (!file) throw new Error('no file')
      return api.upload<ImportResult>(
        `/banking/accounts/${accountId}/statements`,
        file,
      )
    },
    onSuccess: (result) => {
      setFile(null)
      if (inputRef.current) inputRef.current.value = ''
      onDone(result)
    },
    onError: (err) => onError(describeError(err, t)),
  })

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={t.banking.importStatement}
      closeLabel={t.common.close}
      footer={
        <>
          <Button onClick={onClose} disabled={act.isPending}>
            {t.common.cancel}
          </Button>
          <Button
            variant="primary"
            loading={act.isPending}
            disabled={!file || !accountId}
            onClick={() => act.mutate()}
          >
            {t.banking.importStatement}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <Field label={t.banking.account} htmlFor="import-account" required>
          <Select
            id="import-account"
            value={accountId}
            onChange={(event) => setAccountId(event.target.value)}
          >
            <option value="">—</option>
            {accounts.map((account) => (
              <option key={account.id} value={account.id}>
                {account.label}
              </option>
            ))}
          </Select>
        </Field>
        <Field label={t.banking.filename} htmlFor="import-file" hint={t.banking.fileHint} required>
          <Input
            id="import-file"
            ref={inputRef}
            type="file"
            accept=".csv,.xlsx,.xlsm"
            onChange={(event) => setFile(event.target.files?.[0] ?? null)}
          />
        </Field>
      </div>
    </Dialog>
  )
}
