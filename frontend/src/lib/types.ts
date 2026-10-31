/** Response shapes mirroring the backend's Pydantic schemas. */

export type ModuleStatus = 'available' | 'partial' | 'planned'

export interface ModuleInfo {
  key: string
  phase: number
  status: ModuleStatus
  required_permission: string | null
  available_features: string[]
  missing_features: string[]
  visible_to_caller: boolean
}

export interface MembershipSummary {
  id: string
  organization_id: string
  organization_name: string
  organization_code: string
  job_title: string | null
  is_primary_owner: boolean
  role_codes: string[]
}

export interface CurrentUser {
  id: string
  email: string
  full_name: string
  locale: string
  is_superuser: boolean
  must_change_password: boolean
  active_membership: MembershipSummary | null
  memberships: MembershipSummary[]
  permissions: string[]
}

export interface TokenResponse {
  access_token: string
  refresh_token: string
  token_type: string
  expires_at: string
  must_change_password: boolean
}

export interface Organization {
  id: string
  code: string
  legal_name: string
  short_name: string | null
  tax_number: string | null
  legal_form: string | null
  activity_description: string | null
  address: string | null
  phone: string | null
  email: string | null
  registered_on: string | null
  base_currency: string
  timezone: string
  default_locale: string
  is_active: boolean
}

export interface Currency {
  code: string
  name_en: string
  name_uz: string
  name_ru: string
  symbol: string | null
  decimal_places: number
  is_active: boolean
}

export interface OrganizationUser {
  id: string
  email: string
  full_name: string
  phone: string | null
  locale: string
  is_active: boolean
  last_login_at: string | null
  membership_id: string
  job_title: string | null
  membership_active: boolean
  is_primary_owner: boolean
  role_codes: string[]
}

export interface Role {
  id: string
  code: string
  name_uz: string
  name_ru: string
  name_en: string
  description: string | null
  is_system: boolean
  is_read_only: boolean
  permission_codes: string[]
}

export interface PermissionCatalogEntry {
  code: string
  module: string
  description: string
  is_sensitive: boolean
}

export interface AuditLogEntry {
  id: string
  actor_label: string
  action: string
  outcome: 'success' | 'failure' | 'denied'
  target_type: string | null
  target_id: string | null
  target_label: string | null
  business_event: string | null
  reason: string | null
  old_values: Record<string, unknown> | null
  new_values: Record<string, unknown> | null
  request_id: string | null
  ip_address: string | null
  occurred_at: string
}

export interface AuditLogPage {
  items: AuditLogEntry[]
  total_returned: number
  next_before: string | null
}


// --- accounting -------------------------------------------------------------
//
// Every monetary field is a decimal **string**. A JSON number is a float in
// JavaScript, and a large som amount would lose precision. Format these for
// display; never pass one through `Number()`.

export type AccountLevel =
  | 'PART'
  | 'SECTION'
  | 'GROUP'
  | 'ACCOUNT'
  | 'SUBACCOUNT'
  | 'SYNTHETIC'

export type AccountNature =
  | 'ASSET'
  | 'LIABILITY_EQUITY'
  | 'CONTRA_ASSET'
  | 'CONTRA_LIABILITY'
  | 'TRANSIT'
  | 'OFF_BALANCE'

export type AccountElement =
  | 'ASSET'
  | 'LIABILITY'
  | 'EQUITY'
  | 'REVENUE'
  | 'EXPENSE'
  | 'RESULT'

export type EntryStatus = 'DRAFT' | 'POSTED'
export type PeriodStatus = 'OPEN' | 'CLOSED'

export interface Account {
  id: string
  code: string
  name: string
  name_uz: string | null
  name_ru: string | null
  level: AccountLevel
  nature: AccountNature | null
  element: AccountElement | null
  statutory_part: string | null
  parent_id: string | null
  is_postable: boolean
  is_active: boolean
  /** True while the statutory classification is unconfirmed. */
  needs_confirmation: boolean
  description: string | null
}

export interface AccountWithBalance extends Account {
  balance: string
}

export interface SeedChartResult {
  accounts: number
  postable: number
  structural: number
  parents_linked: number
  notice: string
}

export interface FiscalYear {
  id: string
  code: string
  starts_on: string
  ends_on: string
  status: 'OPEN' | 'CLOSED'
}

export interface AccountingPeriod {
  id: string
  fiscal_year_id: string
  code: string
  starts_on: string
  ends_on: string
  status: PeriodStatus
  closed_at: string | null
  reopened_reason: string | null
}

export interface JournalLine {
  id: string
  line_number: number
  account_id: string
  debit: string
  credit: string
  description: string | null
  account_code: string
  account_name: string
}

export interface JournalEntry {
  id: string
  entry_number: string
  entry_date: string
  period_id: string
  status: EntryStatus
  description: string
  currency: string
  source_kind: string
  source_id: string | null
  operation_code: string | null
  reversal_of_entry_id: string | null
  reversed_by_entry_id: string | null
  reason: string | null
  posted_at: string | null
  total: string
}

export interface JournalEntryDetail extends JournalEntry {
  lines: JournalLine[]
}

export interface TrialBalanceRow {
  account_id: string
  account_code: string
  account_name: string
  nature: AccountNature | null
  element: AccountElement | null
  opening_debit: string
  opening_credit: string
  period_debit: string
  period_credit: string
  closing_debit: string
  closing_credit: string
}

export interface TrialBalance {
  from_date: string
  to_date: string
  currency: string
  rows: TrialBalanceRow[]
  total_opening_debit: string
  total_opening_credit: string
  total_period_debit: string
  total_period_credit: string
  total_closing_debit: string
  total_closing_credit: string
  /** False would mean the ledger was corrupted; the UI shows it either way. */
  is_balanced: boolean
}

export interface LedgerLine {
  entry_id: string
  entry_number: string
  entry_date: string
  description: string
  line_description: string | null
  debit: string
  credit: string
  running_balance: string
  is_reversal: boolean
  reversed: boolean
}

export interface AccountStatement {
  account_id: string
  account_code: string
  account_name: string
  nature: AccountNature | null
  from_date: string
  to_date: string
  opening_balance: string
  closing_balance: string
  total_debit: string
  total_credit: string
  lines: LedgerLine[]
}


// --- sales ------------------------------------------------------------------

export type InvoiceStatus =
  | 'DRAFT'
  | 'ISSUED'
  | 'PARTIALLY_PAID'
  | 'PAID'
  | 'CANCELLED'
  | 'VOID'

export type PaymentMethod = 'CASH' | 'CARD' | 'BANK_TRANSFER'
export type PaymentStatus = 'RECORDED' | 'REFUNDED'
export type CustomerKind = 'PERSON' | 'COMPANY'
export type RevenueTaxTreatment = 'UNDECLARED' | 'NONE' | 'VAT_INCLUSIVE'

export interface Customer {
  id: string
  code: string
  display_name: string
  kind: CustomerKind
  tax_number: string | null
  phone: string | null
  email: string | null
  is_active: boolean
}

export interface CustomerBalance {
  customer_id: string
  invoiced: string
  paid: string
  outstanding: string
  /** Money received with nothing yet to apply it to — a liability. */
  unapplied_credit: string
}

export interface ServiceItem {
  id: string
  code: string
  name: string
  name_uz: string | null
  name_ru: string | null
  category: string
  mxik_code: string | null
  unit_label: string | null
  is_active: boolean
  current_price: string | null
  price_effective_from: string | null
}

export interface ServicePrice {
  id: string
  amount: string
  currency: string
  effective_from: string
  effective_to: string | null
}

export interface InvoiceLine {
  id: string
  line_number: number
  service_id: string | null
  description: string
  quantity: string
  unit_price: string
  line_total: string
  mxik_code: string | null
}

export interface Invoice {
  id: string
  number: string
  customer_id: string
  status: InvoiceStatus
  issue_date: string
  due_date: string | null
  currency: string
  subtotal: string
  tax_total: string
  total: string
  tax_treatment: string
  notes: string | null
  issued_at: string | null
  voided_at: string | null
  void_reason: string | null
  allocated: string
  outstanding: string
}

export interface InvoiceDetail extends Invoice {
  lines: InvoiceLine[]
  customer_name: string
}

export interface PaymentAllocation {
  id: string
  payment_id: string
  invoice_id: string
  amount: string
}

export interface Payment {
  id: string
  number: string
  customer_id: string
  received_on: string
  method: PaymentMethod
  status: PaymentStatus
  amount: string
  currency: string
  reference: string | null
  notes: string | null
  refunded_at: string | null
  refund_reason: string | null
  allocated: string
  advance: string
}

export interface PaymentDetail extends Payment {
  allocations: PaymentAllocation[]
  customer_name: string
}

export interface AgeingBucket {
  label: string
  from_days: number
  to_days: number | null
  amount: string
}

export interface CustomerAgeing {
  customer_id: string
  customer_code: string
  customer_name: string
  total_outstanding: string
  buckets: AgeingBucket[]
}

export interface ReceivablesAgeing {
  as_of: string
  currency: string
  customers: CustomerAgeing[]
  total_outstanding: string
  bucket_totals: AgeingBucket[]
}


// --- cash -------------------------------------------------------------------

export type SessionStatus = 'OPEN' | 'COUNTED' | 'CLOSED'
export type DepositRoute = 'STAFF' | 'COLLECTION'
export type MovementKind =
  | 'OPENING_FLOAT'
  | 'RECEIPT'
  | 'REFUND'
  | 'EXPENSE'
  | 'DEPOSIT'
  | 'ADJUSTMENT_IN'
  | 'ADJUSTMENT_OUT'

export interface CashRegister {
  id: string
  code: string
  name: string
  account_id: string
  currency: string
  is_active: boolean
}

export interface SessionTotals {
  opening_float: string
  receipts: string
  refunds: string
  expenses: string
  deposits: string
  adjustments_in: string
  adjustments_out: string
  /** Derived from the movements; there is no stored field to edit. */
  expected: string
  counted: string | null
  discrepancy: string | null
}

export interface CashMovement {
  id: string
  kind: MovementKind
  amount: string
  description: string | null
  occurred_at: string
  payment_id: string | null
  deposit_route: DepositRoute | null
  reference: string | null
}

export interface CashSession {
  id: string
  register_id: string
  business_date: string
  status: SessionStatus
  currency: string
  opened_at: string
  counted_amount: string | null
  counted_at: string | null
  discrepancy: string | null
  discrepancy_reason: string | null
  approved_at: string | null
  closed_at: string | null
}

export interface CashSessionDetail extends CashSession {
  register_code: string
  register_name: string
  totals: SessionTotals
  movements: CashMovement[]
}


// --- accounting policy ------------------------------------------------------

export type PolicyStatus = 'DRAFT' | 'ACTIVE' | 'SUPERSEDED'

export interface AccountingPolicy {
  id: string
  code: string
  name: string
  version: number
  status: PolicyStatus
  effective_from: string
  /** Set when a later version supersedes this one; it still answers for its own dates. */
  effective_to: string | null
  activated_at: string | null
  notes: string | null
  rule_count: number
}

export interface PolicyRule {
  id: string
  /** The business event this rule answers, e.g. `payment_received_card`. */
  operation_code: string
  description: string
  description_uz: string | null
  description_ru: string | null
  debit_account_id: string
  credit_account_id: string
  debit_account_code: string
  credit_account_code: string
  /** Where the rule came from, kept so a reviewer can check it. */
  required_documents: string | null
}

// --- banking ----------------------------------------------------------------

export type TransactionDirection = 'IN' | 'OUT'
export type TransactionStatus = 'UNMATCHED' | 'MATCHED' | 'IGNORED'
export type MatchableKind = 'CASH_DEPOSIT' | 'CARD_PAYMENT' | 'BANK_PAYMENT'
export type ImportStatus = 'COMPLETED' | 'FAILED'

export interface BankAccount {
  id: string
  code: string
  name: string
  bank_name: string | null
  mfo: string | null
  /** Masked server-side: the full number is a payment credential. */
  account_number_masked: string | null
  /** The ledger account whose balance this account is meant to equal. */
  account_id: string
  currency: string
  is_active: boolean
  has_column_mapping: boolean
}

/** The column headings the importer recognises without configuration. */
export interface ColumnMappingDefaults {
  aliases: Record<string, string[]>
}

export interface ImportResult {
  import_id: string
  rows_read: number
  rows_imported: number
  duplicates_skipped: number
  rows_rejected: number
  problems: string[]
  period_from: string | null
  period_to: string | null
}

export interface StatementImport {
  id: string
  bank_account_id: string
  filename: string
  status: ImportStatus
  rows_read: number
  rows_imported: number
  duplicates_skipped: number
  rows_rejected: number
  period_from: string | null
  period_to: string | null
  problems: string[] | null
  created_at: string
}

export interface BankTransaction {
  id: string
  bank_account_id: string
  value_date: string
  direction: TransactionDirection
  amount: string
  currency: string
  description: string | null
  counterparty_name: string | null
  counterparty_mfo: string | null
  document_number: string | null
  status: TransactionStatus
  ignored_reason: string | null
  /** What the bank or acquirer withheld from this line. */
  commission_amount: string
}

export interface ReconciliationMatch {
  id: string
  source_kind: MatchableKind
  source_id: string
  amount: string
  note: string | null
  created_at: string
}

export interface BankTransactionDetail extends BankTransaction {
  matched_amount: string
  /** What the line still needs explained before it can be reconciled. */
  unaccounted_amount: string
  matches: ReconciliationMatch[]
}

export interface MatchCandidate {
  kind: MatchableKind
  id: string
  label: string
  occurred_on: string
  amount: string
  already_matched: string
  outstanding: string
}
