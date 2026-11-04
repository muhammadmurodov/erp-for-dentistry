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
  /**
   * Declared by the owner: too few people here to put a different person on
   * each side of an approval control. It grants nobody a permission; it
   * removes only the requirement that an approver be somebody else.
   */
  duties_cannot_be_separated: boolean
  duties_declared_at: string | null
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
  /**
   * False for a role this organization has no use for — a middle-management
   * role in a three-person clinic. The assignment screen leaves it out rather
   * than offering a choice whose only outcome is a refusal.
   */
  offered_for_assignment: boolean
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
  /** Given up on. The revenue stands; the receivable does not. */
  | 'WRITTEN_OFF'

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

// --- clinic -----------------------------------------------------------------

export interface PatientSummary {
  id: string
  code: string
  display_name: string
  family_name: string
  given_name: string
  patronymic: string | null
  birth_year: number | null
  phone: string | null
  address: string | null
  registered_on: string
  assigned_dentist_user_id: string | null
  customer_id: string | null
  is_active: boolean
  merged_into_patient_id: string | null
}

export interface DuplicateCandidate {
  patient_id: string
  code: string
  display_name: string
  birth_year: number | null
  phone: string | null
  reason: string
}

export interface PatientRegistered {
  patient: PatientSummary
  duplicate_candidates: DuplicateCandidate[]
}

/**
 * A suspected duplicate with both records attached.
 *
 * Nothing in this queue has been merged. Registration is never refused on a
 * suspicion, so a row here means two records look alike and nobody has
 * decided yet.
 */
/**
 * The clinic's day, for the dashboard.
 *
 * A `null` field means the signed-in person holds no permission to read that
 * part of the clinic — **absent, not zero**. Rendering a withheld figure as
 * `0` would tell a receptionist the clinic is owed nothing.
 *
 * Every amount is derived on the server from the records themselves, so these
 * figures agree with the ageing report and the cash session by construction
 * rather than by coincidence.
 */
export interface TodayView {
  business_date: string
  currency: string
  appointments: {
    total: number
    /** Booked and still awaited: not arrived, cancelled or missed. */
    expected: number
    arrived: number
    completed: number
    cancelled: number
    no_show: number
  } | null
  patients_registered: number | null
  treatments_in_progress: number | null
  treatments_billable: number | null
  payments: {
    cash: string
    card: string
    transfer: string
    total: string
    count: number
  } | null
  debt_outstanding: string | null
  advances_available: string | null
  drawer: {
    session_id: string
    register_code: string
    business_date: string
    expected: string
    /** An open session dated before today: the till was never closed. */
    from_an_earlier_day: boolean
  } | null
  /** Distinguishes "no drawer open" from "not your business". */
  drawer_readable: boolean
  duplicate_reviews_pending: number | null
}

export interface DuplicateReviewDetail {
  id: string
  reason: string
  status: 'PENDING' | 'MERGED' | 'DISTINCT'
  created_at: string
  decided_at: string | null
  decision_note: string | null
  /** The record the review was raised for: normally the newer one. */
  patient: PatientSummary
  /** The record it may be a duplicate of. */
  candidate: PatientSummary
  /** Which record a merge would keep. Known before anybody acts. */
  survivor_id: string
}

export type EncounterStatus = 'OPEN' | 'COMPLETED' | 'CANCELLED'

/** One visit to the chair. */
export interface Encounter {
  id: string
  code: string
  patient_id: string
  appointment_id: string | null
  dentist_user_id: string | null
  occurred_on: string
  status: EncounterStatus
  complaint: string | null
  clinical_notes: string | null
  body_weight_kg: string | null
  completed_at: string | null
}

export type TreatmentItemStatus =
  | 'PLANNED'
  | 'IN_PROGRESS'
  | 'COMPLETED'
  | 'CANCELLED'
  | 'CORRECTED'

/** One piece of work, on one tooth. `COMPLETED` is the only billable state. */
export interface TreatmentItem {
  id: string
  patient_id: string
  encounter_id: string | null
  plan_id: string | null
  service_id: string
  dentist_user_id: string | null
  tooth_number: number | null
  tooth_surfaces: string | null
  finding: string | null
  status: TreatmentItemStatus
  planned_on: string | null
  completed_on: string | null
  quantity: string
  unit_price: string | null
  /** Set once the item reaches an invoice. The money lives there, not here. */
  invoice_line_id: string | null
  corrected_by_item_id: string | null
}

// --- purchasing -------------------------------------------------------------

export type RequestStatus =
  | 'DRAFT'
  | 'SUBMITTED'
  | 'APPROVED'
  | 'REJECTED'
  | 'ORDERED'
  | 'CANCELLED'

export type OrderStatus =
  | 'DRAFT'
  | 'APPROVED'
  | 'SENT'
  | 'PARTIALLY_RECEIVED'
  | 'RECEIVED'
  | 'CANCELLED'
  | 'CLOSED'

export type BillKind = 'MATERIALS' | 'EXPENSE'
export type BillStatus = 'DRAFT' | 'REGISTERED' | 'VOID'
export type SupplierPaymentMethod = 'CASH' | 'BANK' | 'CARD'
export type SupplierPaymentStatus = 'RECORDED' | 'REVERSED'

export interface Supplier {
  id: string
  code: string
  name: string
  tax_number: string | null
  phone: string | null
  email: string | null
  address: string | null
  notes: string | null
  is_active: boolean
}

/** What is owed to one supplier, and what has been paid ahead. */
export interface SupplierBalance {
  supplier_id: string
  billed: string
  paid: string
  outstanding: string
  advances_available: string
  currency: string
}

export interface PurchaseRequest {
  id: string
  code: string
  status: RequestStatus
  requested_by_user_id: string | null
  needed_by: string | null
  reason: string | null
  decided_by_user_id: string | null
  decided_at: string | null
  decision_note: string | null
  self_approved: boolean
}

export interface RequestLine {
  id: string
  description: string
  quantity: string
  unit: string | null
  estimated_unit_price: string | null
}

export interface PurchaseRequestDetail extends PurchaseRequest {
  lines: RequestLine[]
}

export interface OrderLine {
  id: string
  description: string
  quantity: string
  unit: string | null
  unit_price: string
  line_total: string
}

/** One order line and how much of it is still to come. */
export interface OutstandingLine {
  order_line_id: string
  description: string
  unit: string | null
  ordered: string
  received: string
  outstanding: string
}

/** A commitment to a supplier. Nothing about it posts to the ledger. */
export interface PurchaseOrder {
  id: string
  code: string
  supplier_id: string
  supplier_name: string
  request_id: string | null
  status: OrderStatus
  ordered_on: string
  expected_on: string | null
  currency: string
  total: string
  notes: string | null
  raised_by_user_id: string | null
  approved_by_user_id: string | null
  approved_at: string | null
  self_approved: boolean
  closed_reason: string | null
}

export interface PurchaseOrderDetail extends PurchaseOrder {
  lines: OrderLine[]
  outstanding: OutstandingLine[]
}

export interface ReceiptLine {
  id: string
  receipt_id: string
  order_line_id: string | null
  description: string
  quantity: string
  unit: string | null
  rejected_quantity: string
  rejected_reason: string | null
}

export interface GoodsReceipt {
  id: string
  code: string
  supplier_id: string
  order_id: string | null
  received_on: string
  received_by_user_id: string | null
  delivery_note: string | null
  notes: string | null
}

export interface GoodsReceiptDetail extends GoodsReceipt {
  lines: ReceiptLine[]
}

/** A delivery line nobody has billed for yet. */
export interface UninvoicedLine {
  id: string
  receipt_id: string
  receipt_code: string
  received_on: string
  description: string
  quantity: string
  unit: string | null
}

export interface BillLine {
  id: string
  description: string
  quantity: string
  unit: string | null
  unit_price: string
  line_total: string
  receipt_line_id: string | null
}

/**
 * A supplier's invoice.
 *
 * `paid` and `remaining` are derived from live allocations on every read —
 * there is no stored balance anywhere behind them.
 */
export interface SupplierInvoice {
  id: string
  code: string
  supplier_id: string
  supplier_name: string
  order_id: string | null
  supplier_reference: string | null
  kind: BillKind
  status: BillStatus
  invoice_date: string
  due_date: string | null
  currency: string
  total: string
  paid: string
  remaining: string
  registered_at: string | null
  void_reason: string | null
  notes: string | null
}

export interface SupplierInvoiceDetail extends SupplierInvoice {
  lines: BillLine[]
}

export interface SupplierPayment {
  id: string
  code: string
  supplier_id: string
  supplier_name: string
  amount: string
  currency: string
  method: SupplierPaymentMethod
  status: SupplierPaymentStatus
  paid_on: string
  reference: string | null
  notes: string | null
  /** The part not applied to any bill — an advance held with the supplier. */
  unapplied: string
}

export interface PayablesAgeingRow {
  supplier_id: string
  supplier_name: string
  current: string
  days_1_30: string
  days_31_60: string
  days_61_90: string
  days_over_90: string
  total: string
}

// --- employees --------------------------------------------------------------

export type EmploymentStatus =
  | 'PROSPECTIVE'
  | 'ACTIVE'
  | 'SUSPENDED'
  | 'ENDED'

export type AttendanceCode =
  | 'WORKED'
  | 'ABSENT'
  | 'SICK'
  | 'LEAVE'
  | 'HOLIDAY'

export interface JobPosition {
  id: string
  code: string
  name: string
  description: string | null
  is_active: boolean
}

/**
 * Somebody the clinic employs.
 *
 * `role_codes` is read from the linked login's membership on every request,
 * never stored on the employee — one person may hold several roles, and this
 * module must not become a second answer to what they may do.
 */
export interface Employee {
  id: string
  code: string
  display_name: string
  family_name: string
  given_name: string
  patronymic: string | null
  phone: string | null
  address: string | null
  position_id: string | null
  position_name: string | null
  user_id: string | null
  role_codes: string[]
  status: EmploymentStatus
  hired_on: string
  ended_on: string | null
  ended_reason: string | null
  notes: string | null
}

/** A member of this organization with no employee record yet. */
export interface LinkableUser {
  id: string
  email: string
  full_name: string
}

/** One weekday of somebody's ordinary week. ISO weekday: 1 Monday … 7 Sunday. */
export interface ScheduleDay {
  weekday: number
  hours: string
}

/** What one person's one day actually was. Hours only on a day worked. */
export interface AttendanceDay {
  id: string
  employee_id: string
  on_date: string
  code: AttendanceCode
  hours: string | null
  note: string | null
  recorded_by_user_id: string | null
}

export interface AttendanceSummary {
  employee_id: string
  from_date: string
  to_date: string
  days_present: number
  days_absent: number
  days_sick: number
  days_leave: number
  days_holiday: number
  hours: string
}

export type PlanStatus =
  | 'DRAFT'
  | 'PROPOSED'
  | 'ACCEPTED'
  | 'IN_PROGRESS'
  | 'COMPLETED'
  | 'ABANDONED'

/**
 * A course of treatment spanning several visits.
 *
 * It holds work together and carries no money: no price, no total, no
 * invoice. Billing still happens from completed items, one at a time, which
 * is why nothing here resembles a quotation.
 */
export interface TreatmentPlan {
  id: string
  code: string
  patient_id: string
  title: string
  status: PlanStatus
  started_on: string | null
  completed_on: string | null
  notes: string | null
}

export interface TreatmentPlanDetail extends TreatmentPlan {
  dentist_user_id: string | null
  items: TreatmentItem[]
  /** Items still PLANNED or IN_PROGRESS; the plan cannot finish while > 0. */
  open_item_count: number
}

export type AppointmentStatusValue =
  | 'SCHEDULED'
  | 'CONFIRMED'
  | 'ARRIVED'
  | 'COMPLETED'
  | 'CANCELLED'
  | 'NO_SHOW'

/**
 * A booking, with the names the day's list has to show.
 *
 * `scheduled_on` and `scheduled_at` are a local date and a local time in the
 * clinic's own zone — not an instant. Putting them through a timezone
 * conversion would move the appointment.
 */
export interface Appointment {
  id: string
  patient_id: string
  dentist_user_id: string | null
  scheduled_on: string
  /** `HH:MM:SS`, or `null` for "that day". */
  scheduled_at: string | null
  duration_minutes: number | null
  status: AppointmentStatusValue
  reason: string | null
  cancelled_reason: string | null
  patient_code: string
  patient_name: string
  patient_phone: string | null
  dentist_name: string | null
  /** The visit opened against this booking, where there is one. */
  encounter_id: string | null
}

/**
 * Somebody a booking may be given to.
 *
 * Deliberately thin: the appointment form needs the people who sit in the
 * chairs, and reception — who books the day — may not read the staff
 * directory. The server answers a name and an id, both of which already
 * appear in the day beside the form.
 */
export interface ClinicDentist {
  id: string
  full_name: string
}

/** One partial refund of a payment. */
export interface PaymentRefundRow {
  id: string
  number: string
  payment_id: string
  amount: string
  currency: string
  refunded_on: string
  reason: string
  self_approved: boolean
}

/**
 * What of a payment may still be given back.
 *
 * Every figure is derived on the server from the payment, its allocations and
 * its previous refunds — never worked out in the browser, because a stale
 * figure would offer money that is no longer there.
 */
export interface RefundabilityView {
  payment: Payment
  amount: string
  allocated: string
  refunded: string
  refundable: string
  currency: string
  may_refund: boolean
  /** True where the money has to come out of an open cash drawer. */
  needs_cash_session: boolean
  refunds: PaymentRefundRow[]
}

export interface PatientBalance {
  customer_id: string | null
  invoiced: string
  paid: string
  /** The qarzdorlik: billed, not paid, not written off. */
  outstanding: string
  /** Oldindan to'lov — money received that no invoice has claimed yet. */
  advances_available: string
  written_off: string
}

/**
 * How far a bill has been paid. Derived by the backend from the invoice total
 * and its live allocations, never stored — so it cannot disagree with the
 * payments behind it.
 */
export type SettlementStatus =
  | 'UNPAID'
  | 'PARTIALLY_PAID'
  | 'FULLY_PAID'
  | 'WRITTEN_OFF'
  | 'NOT_PAYABLE'

export interface PatientInvoiceRow {
  invoice_id: string
  number: string
  issue_date: string
  status: InvoiceStatus
  settlement_status: SettlementStatus
  total: string
  paid: string
  remaining: string
  currency: string
}

export interface PatientDetail {
  patient: PatientSummary
  /** `null` where the reader may not see money — a dentist, for instance. */
  balance: PatientBalance | null
  invoices: PatientInvoiceRow[] | null
  billable_items: number
  open_duplicate_reviews: number
}

/**
 * The payment panel's state, all of it computed by the backend.
 *
 * `remaining` is displayed and never sent back: the settlement request has no
 * field for it.
 */
export interface SettlementView {
  invoice_id: string
  invoice_number: string
  customer_id: string
  currency: string
  invoice_status: InvoiceStatus
  status: SettlementStatus
  treatment_total: string
  paid: string
  paid_cash: string
  paid_card: string
  paid_other: string
  remaining: string
  advances_available: string
  max_payable: string
  written_off: string
  accepts_payment: boolean
  /** Null means no drawer is open, so cash cannot be taken here. */
  open_cash_session_id: string | null
}

export interface SettlementResult {
  view: SettlementView
  applied_advances: string
  cash_payment_id: string | null
  card_payment_id: string | null
  /** True when the submission had already been carried out; money moved once. */
  replayed: boolean
}

export interface SettlementHistoryRow {
  allocation_id: string
  payment_id: string
  payment_number: string
  method: PaymentMethod
  amount: string
  received_on: string
  reversed_at: string | null
  reversal_reason: string | null
}

// --- inventory --------------------------------------------------------------

/**
 * Materials and the ledger of what moved.
 *
 * Quantities are strings for the same reason money is: a JavaScript number
 * loses precision, and a stock count that reads 2.9999999999999996 gets
 * written off by somebody.
 */
export interface InventoryItem {
  id: string
  code: string
  name: string
  unit: string
  category_id: string | null
  reorder_level: string | null
  is_batch_tracked: boolean
  notes: string | null
  is_active: boolean
}

export interface InventoryCategory {
  id: string
  code: string
  name: string
  is_active: boolean
}

export interface StockLocation {
  id: string
  code: string
  name: string
  is_default: boolean
  is_active: boolean
}

export interface StockBatch {
  id: string
  item_id: string
  code: string
  expires_on: string | null
  notes: string | null
}

/** One shelf, one material, one lot — summed from the movements. */
export interface StockBalanceRow {
  item_id: string
  item_code: string
  item_name: string
  unit: string
  location_id: string
  batch_id: string | null
  batch_code: string | null
  expires_on: string | null
  quantity: string
}

export interface LowStockRow {
  item_id: string
  code: string
  name: string
  unit: string
  on_hand: string
  reorder_level: string
}

export interface ExpiringRow {
  item_id: string
  item_name: string
  unit: string
  batch_id: string
  batch_code: string
  expires_on: string
  quantity: string
  days_remaining: number
}

export type StockMovementKind =
  | 'RECEIPT'
  | 'CONSUMPTION'
  | 'ADJUSTMENT'
  | 'WRITE_OFF'
  | 'TRANSFER_IN'
  | 'TRANSFER_OUT'

export type StockMovementSource =
  | 'GOODS_RECEIPT_LINE'
  | 'TREATMENT_ITEM'
  | 'STOCK_ADJUSTMENT'
  | 'STOCK_TRANSFER'
  | 'MANUAL'

export interface StockMovement {
  id: string
  item_id: string
  location_id: string
  batch_id: string | null
  kind: StockMovementKind
  quantity: string
  occurred_on: string
  source_kind: StockMovementSource
  source_id: string | null
  reason: string | null
}

export type AdjustmentStatus = 'DRAFT' | 'PROPOSED' | 'APPROVED' | 'REJECTED'

export interface StockCount {
  id: string
  code: string
  location_id: string
  counted_on: string
  status: AdjustmentStatus
  reason: string
  counted_by_user_id: string | null
  decided_by_user_id: string | null
  decision_note: string | null
}

export interface StockCountLine {
  id: string
  item_id: string
  batch_id: string | null
  system_quantity: string
  counted_quantity: string
  note: string | null
}

export interface StockCountDetail extends StockCount {
  lines: StockCountLine[]
}

export type InventoryValuationMethod = 'UNDECLARED' | 'WEIGHTED_AVERAGE' | 'FIFO'

export interface ValuationRow {
  item_id: string
  code: string
  name: string
  unit: string
  quantity: string
  unit_cost: string | null
  value: string | null
}

/**
 * Stock valued, or an honest statement that it cannot be.
 *
 * While `method` is `UNDECLARED` every `unit_cost` is null and `total_value`
 * is null, but the quantities are real: the clinic sees what it has without
 * the system inventing what it is worth.
 */
export interface StockValuation {
  method: InventoryValuationMethod
  currency: string
  total_value: string | null
  unpriced_items: number
  rows: ValuationRow[]
}

