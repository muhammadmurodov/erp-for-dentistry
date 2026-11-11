/**
 * The filing cabinet.
 *
 * Three things on this screen exist because of a decision in the backend,
 * and the screen states each one rather than hiding it:
 *
 *  - **Archive, not delete.** There is no delete control, because there is
 *    no delete endpoint; the retention policy is undecided.
 *  - **Verify the store.** A backup nobody has read back is a belief about a
 *    disk. This is the button that checks it.
 *  - **What may be uploaded is told in advance**, from `/documents/
 *    capabilities`, so a person does not discover the limit by hitting it.
 */

import { useMemo, useRef, useState } from 'react'
import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseQueryResult,
} from '@tanstack/react-query'
import { api, ApiError, requestBlob } from '@/lib/api'
import { useI18n } from '@/i18n'
import { useAuth } from '@/features/auth/AuthProvider'
import { describeError } from '@/lib/errorMessage'
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
  PageHeader,
  Select,
  Table,
  Td,
  Textarea,
  Th,
} from '@/components/ui'
import type {
  DocumentCapabilities,
  DocumentCategory,
  DocumentEntityType,
  StoreVerification,
  StoredDocument,
} from './types'

/**
 * The record types a person can pick from a list on this screen.
 *
 * The API accepts eleven; these two have a directory somebody can browse and
 * are what a clinic actually files paper against. The rest are reachable
 * over the API and will get a documents panel on their own screen, which is
 * the right place to file a bill against a bill.
 */
const PICKABLE: { type: DocumentEntityType; path: string; permission: string }[] = [
  { type: 'PATIENT', path: '/clinic/patients', permission: 'clinic.patient.read' },
  { type: 'EMPLOYEE', path: '/hr/employees', permission: 'hr.employee.read' },
]

interface PickableRecord {
  id: string
  code: string
  display_name: string
}

const CATEGORIES: DocumentCategory[] = [
  'CONTRACT',
  'INVOICE',
  'RECEIPT',
  'BANK_STATEMENT',
  'IDENTITY',
  'CLINICAL',
  'PAYROLL',
  'STATUTORY',
  'OTHER',
]

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

export function DocumentsPage() {
  const { t, locale } = useI18n()
  const { can } = useAuth()
  const queryClient = useQueryClient()

  const [category, setCategory] = useState<DocumentCategory | ''>('')
  const [search, setSearch] = useState('')
  const [includeHidden, setIncludeHidden] = useState(false)
  const [uploadOpen, setUploadOpen] = useState(false)
  const [archiving, setArchiving] = useState<StoredDocument | null>(null)
  const [verification, setVerification] = useState<StoreVerification | null>(null)
  const [banner, setBanner] = useState<string | null>(null)

  const mayManage = can('document.manage')

  const filters = { category, search, includeHidden }
  const documents: UseQueryResult<StoredDocument[]> = useQuery({
    queryKey: ['documents', filters],
    queryFn: () =>
      api.get<StoredDocument[]>('/documents', {
        category: category || undefined,
        search: search || undefined,
        include_hidden: includeHidden || undefined,
      }),
  })

  const capabilities = useQuery({
    queryKey: ['documents', 'capabilities'],
    queryFn: () => api.get<DocumentCapabilities>('/documents/capabilities'),
    staleTime: 5 * 60_000,
  })

  const refresh = () =>
    queryClient.invalidateQueries({ queryKey: ['documents'] })

  const verify = useMutation({
    mutationFn: () => api.get<StoreVerification>('/documents/verify'),
    onSuccess: (result) => {
      setVerification(result)
      setBanner(null)
    },
    onError: (error) => setBanner(describeError(error, t)),
  })

  const archive = useMutation({
    mutationFn: ({ id, reason }: { id: string; reason: string }) =>
      api.post<StoredDocument>(`/documents/${id}/archive`, { reason }),
    onSuccess: async () => {
      setArchiving(null)
      await refresh()
    },
    onError: (error) => setBanner(describeError(error, t)),
  })

  const restore = useMutation({
    mutationFn: (id: string) =>
      api.post<StoredDocument>(`/documents/${id}/restore`),
    onSuccess: refresh,
    onError: (error) => setBanner(describeError(error, t)),
  })

  async function download(document_: StoredDocument) {
    try {
      const blob = await requestBlob(`/documents/${document_.id}/content`, {
        method: 'GET',
      })
      const url = URL.createObjectURL(blob)
      const anchor = document.createElement('a')
      anchor.href = url
      anchor.download = document_.original_filename
      anchor.click()
      URL.revokeObjectURL(url)
      setBanner(null)
    } catch (error) {
      // A refused download is the integrity check working, so the message
      // from the server is shown rather than a generic failure.
      setBanner(
        error instanceof ApiError ? error.message : describeError(error, t),
      )
    }
  }

  if (!can('document.read')) {
    return (
      <>
        <PageHeader title={t.documents.title} />
        <Alert tone="warning">{t.documents.noAccess}</Alert>
      </>
    )
  }

  const rows = documents.data ?? []

  return (
    <>
      <PageHeader
        title={t.documents.title}
        description={t.documents.subtitle}
        actions={
          mayManage ? (
            <div className="flex gap-2">
              <Button
                variant="secondary"
                onClick={() => verify.mutate()}
                disabled={verify.isPending}
              >
                {verify.isPending ? t.documents.verifying : t.documents.verify}
              </Button>
              <Button onClick={() => setUploadOpen(true)}>
                {t.documents.upload}
              </Button>
            </div>
          ) : null
        }
      />

      {banner ? <Alert tone="danger">{banner}</Alert> : null}

      {verification ? (
        <Alert tone={verification.ok ? 'success' : 'danger'}>
          <div className="flex flex-col gap-1">
            <span>
              {verification.ok
                ? t.documents.verifyOk
                : t.documents.verifyFailed}
            </span>
            <span className="text-xs">
              {t.documents.verifyChecked}: {verification.checked} ·{' '}
              {t.documents.verifyCorrupted}: {verification.corrupted.length} ·{' '}
              {t.documents.verifyMissing}: {verification.missing.length}
            </span>
          </div>
        </Alert>
      ) : null}

      <Card>
        <CardHeader
          title={t.documents.title}
          description={t.documents.retentionNote}
        />

        <div className="mb-4 flex flex-wrap items-end gap-3">
          <Field label={t.documents.category}>
            <Select
              value={category}
              onChange={(event) =>
                setCategory(event.target.value as DocumentCategory | '')
              }
            >
              <option value="">{t.documents.allCategories}</option>
              {CATEGORIES.map((value) => (
                <option key={value} value={value}>
                  {t.documents[`category${value}` as const]}
                </option>
              ))}
            </Select>
          </Field>
          <Field label={t.documents.search}>
            <Input
              value={search}
              placeholder={t.documents.searchPlaceholder}
              onChange={(event) => setSearch(event.target.value)}
            />
          </Field>
          <label className="mb-1 flex items-center gap-2 text-sm text-[var(--text-secondary)]">
            <input
              type="checkbox"
              checked={includeHidden}
              onChange={(event) => setIncludeHidden(event.target.checked)}
            />
            {t.documents.showArchived}
          </label>
        </div>

        {documents.isLoading ? (
          <LoadingState label={t.common.loading} />
        ) : documents.isError ? (
          <ErrorState
            title={t.errors.loadFailedTitle}
            message={describeError(documents.error, t)}
            onRetry={() => documents.refetch()}
            retryLabel={t.common.retry}
          />
        ) : rows.length === 0 ? (
          <EmptyState
            title={t.documents.nothingYet}
            body={t.documents.nothingYetBody}
          />
        ) : (
          <Table>
            <thead>
              <tr>
                <Th>{t.documents.documentTitle}</Th>
                <Th>{t.documents.category}</Th>
                <Th>{t.documents.attachTo}</Th>
                <Th>{t.documents.size}</Th>
                <Th>{t.documents.uploadedBy}</Th>
                <Th>{t.documents.uploadedOn}</Th>
                <Th>{t.common.actions}</Th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.id}>
                  <Td>
                    <div className="flex flex-col">
                      <span className="font-medium">{row.title}</span>
                      <span className="text-xs text-[var(--text-muted)]">
                        {row.original_filename}
                      </span>
                      {row.status !== 'STORED' ? (
                        <span className="mt-1">
                          <Badge tone="neutral">
                            {row.status === 'ARCHIVED'
                              ? t.documents.statusArchived
                              : t.documents.statusSuperseded}
                          </Badge>
                        </span>
                      ) : null}
                    </div>
                  </Td>
                  <Td>{t.documents[`category${row.category}` as const]}</Td>
                  <Td>
                    {row.entity_type ? (
                      <span className="text-xs">{row.entity_type}</span>
                    ) : (
                      <span className="text-xs text-[var(--text-muted)]">
                        {t.documents.attachToNothing}
                      </span>
                    )}
                  </Td>
                  <Td>{formatBytes(row.byte_size)}</Td>
                  <Td>{row.uploaded_by_name ?? '—'}</Td>
                  <Td>
                    {new Date(row.created_at).toLocaleDateString(
                      locale === 'uz' ? 'uz-UZ' : locale,
                    )}
                  </Td>
                  <Td>
                    <div className="flex flex-wrap gap-2">
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => download(row)}
                      >
                        {t.documents.download}
                      </Button>
                      {mayManage && row.status === 'STORED' ? (
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => setArchiving(row)}
                        >
                          {t.documents.archive}
                        </Button>
                      ) : null}
                      {mayManage && row.status === 'ARCHIVED' ? (
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => restore.mutate(row.id)}
                        >
                          {t.documents.restore}
                        </Button>
                      ) : null}
                    </div>
                  </Td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </Card>

      {uploadOpen ? (
        <UploadDialog
          capabilities={capabilities.data}
          onClose={() => setUploadOpen(false)}
          onUploaded={async () => {
            setUploadOpen(false)
            await refresh()
          }}
        />
      ) : null}

      {archiving ? (
        <ArchiveDialog
          document={archiving}
          pending={archive.isPending}
          onClose={() => setArchiving(null)}
          onConfirm={(reason) =>
            archive.mutate({ id: archiving.id, reason })
          }
        />
      ) : null}
    </>
  )
}

function UploadDialog({
  capabilities,
  onClose,
  onUploaded,
}: {
  capabilities: DocumentCapabilities | undefined
  onClose: () => void
  onUploaded: () => void | Promise<void>
}) {
  const { t } = useI18n()
  const { can } = useAuth()
  const fileRef = useRef<HTMLInputElement>(null)
  const [title, setTitle] = useState('')
  const [description, setDescription] = useState('')
  const [category, setCategory] = useState<DocumentCategory>('CONTRACT')
  const [entityType, setEntityType] = useState<DocumentEntityType | ''>('')
  const [entityId, setEntityId] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [pending, setPending] = useState(false)

  const pickable = PICKABLE.filter((entry) => can(entry.permission))
  const chosen = pickable.find((entry) => entry.type === entityType)

  // Only fetched once a record type is chosen: a person filing the clinic's
  // own registration certificate never loads the patient directory.
  const records = useQuery({
    queryKey: ['documents', 'pickable', entityType],
    queryFn: () => api.get<PickableRecord[]>(chosen!.path),
    enabled: Boolean(chosen),
  })

  const accept = useMemo(
    () => (capabilities?.accepted_content_types ?? []).join(','),
    [capabilities],
  )

  async function submit() {
    const file = fileRef.current?.files?.[0]
    if (!file) {
      setError(t.documents.file)
      return
    }
    if (entityType && !entityId) {
      setError(t.documents.recordId)
      return
    }
    setPending(true)
    setError(null)
    try {
      const form = new FormData()
      form.append('file', file)
      form.append('category', category)
      form.append('title', title.trim() || file.name)
      if (description.trim()) form.append('description', description.trim())
      if (entityType && entityId) {
        form.append('entity_type', entityType)
        form.append('entity_id', entityId)
      }
      await api.post<StoredDocument>('/documents', form)
      await onUploaded()
    } catch (caught) {
      setError(describeError(caught, t))
    } finally {
      setPending(false)
    }
  }

  return (
    <Dialog
      open
      title={t.documents.uploadTitle}
      closeLabel={t.common.close}
      onClose={onClose}
    >
      <div className="flex flex-col gap-3">
        {error ? <Alert tone="danger">{error}</Alert> : null}
        <Field label={t.documents.file}>
          <input ref={fileRef} type="file" accept={accept} />
        </Field>
        {capabilities ? (
          <p className="text-xs text-[var(--text-muted)]">
            {formatBytes(capabilities.max_upload_bytes)} ·{' '}
            {capabilities.accepted_content_types.length}
          </p>
        ) : null}
        <Field label={t.documents.documentTitle}>
          <Input
            value={title}
            placeholder={t.documents.titlePlaceholder}
            onChange={(event) => setTitle(event.target.value)}
          />
        </Field>
        <Field label={t.documents.category}>
          <Select
            value={category}
            onChange={(event) =>
              setCategory(event.target.value as DocumentCategory)
            }
          >
            {CATEGORIES.map((value) => (
              <option key={value} value={value}>
                {t.documents[`category${value}` as const]}
              </option>
            ))}
          </Select>
        </Field>
        {pickable.length > 0 ? (
          <Field label={t.documents.attachTo}>
            <Select
              value={entityType}
              onChange={(event) => {
                setEntityType(event.target.value as DocumentEntityType | '')
                setEntityId('')
              }}
            >
              <option value="">{t.documents.attachToNothing}</option>
              {pickable.map((entry) => (
                <option key={entry.type} value={entry.type}>
                  {entry.type === 'PATIENT' ? t.nav.clinic : t.nav.employees}
                </option>
              ))}
            </Select>
          </Field>
        ) : null}

        {chosen ? (
          <Field label={t.documents.recordId}>
            <Select
              value={entityId}
              onChange={(event) => setEntityId(event.target.value)}
              disabled={records.isLoading}
            >
              <option value="">
                {records.isLoading ? t.common.loading : '—'}
              </option>
              {(records.data ?? []).map((record) => (
                <option key={record.id} value={record.id}>
                  {record.code} · {record.display_name}
                </option>
              ))}
            </Select>
          </Field>
        ) : null}

        <Field label={t.documents.description}>
          <Textarea
            value={description}
            rows={2}
            onChange={(event) => setDescription(event.target.value)}
          />
        </Field>
        <div className="flex justify-end gap-2">
          <Button variant="secondary" onClick={onClose}>
            {t.common.cancel}
          </Button>
          <Button onClick={submit} disabled={pending}>
            {pending ? t.documents.uploading : t.documents.upload}
          </Button>
        </div>
      </div>
    </Dialog>
  )
}

function ArchiveDialog({
  document,
  pending,
  onClose,
  onConfirm,
}: {
  document: StoredDocument
  pending: boolean
  onClose: () => void
  onConfirm: (reason: string) => void
}) {
  const { t } = useI18n()
  const [reason, setReason] = useState('')

  return (
    <Dialog
      open
      title={t.documents.archiveTitle}
      closeLabel={t.common.close}
      onClose={onClose}
    >
      <div className="flex flex-col gap-3">
        <p className="text-sm text-[var(--text-secondary)]">
          {t.documents.archiveExplain}
        </p>
        <p className="text-sm font-medium">{document.title}</p>
        <Field label={t.documents.archiveReason}>
          <Textarea
            value={reason}
            rows={2}
            onChange={(event) => setReason(event.target.value)}
          />
        </Field>
        <div className="flex justify-end gap-2">
          <Button variant="secondary" onClick={onClose}>
            {t.common.cancel}
          </Button>
          <Button
            onClick={() => onConfirm(reason)}
            disabled={pending || reason.trim().length < 3}
          >
            {t.documents.archive}
          </Button>
        </div>
      </div>
    </Dialog>
  )
}
