/** Shapes the documents API returns. Mirrors `app/modules/documents/schemas.py`. */

export type DocumentCategory =
  | 'CONTRACT'
  | 'INVOICE'
  | 'RECEIPT'
  | 'BANK_STATEMENT'
  | 'IDENTITY'
  | 'CLINICAL'
  | 'PAYROLL'
  | 'STATUTORY'
  | 'OTHER'

export type DocumentEntityType =
  | 'PATIENT'
  | 'EMPLOYEE'
  | 'INVOICE'
  | 'SUPPLIER'
  | 'SUPPLIER_INVOICE'
  | 'PURCHASE_ORDER'
  | 'PAYMENT'
  | 'PAYROLL_RUN'
  | 'BANK_ACCOUNT'
  | 'JOURNAL_ENTRY'
  | 'TREATMENT_PLAN'

export type DocumentStatus = 'STORED' | 'ARCHIVED' | 'SUPERSEDED'

export interface StoredDocument {
  id: string
  category: DocumentCategory
  title: string
  description: string | null
  original_filename: string
  content_type: string
  byte_size: number
  checksum_sha256: string
  entity_type: DocumentEntityType | null
  entity_id: string | null
  status: DocumentStatus
  status_reason: string | null
  replaces_document_id: string | null
  uploaded_by_user_id: string
  uploaded_by_name: string | null
  created_at: string
}

export interface DocumentCapabilities {
  max_upload_bytes: number
  accepted_content_types: string[]
  categories: DocumentCategory[]
  entity_types: DocumentEntityType[]
}

export interface StoreVerification {
  checked: number
  intact: number
  corrupted: string[]
  missing: string[]
  ok: boolean
}
