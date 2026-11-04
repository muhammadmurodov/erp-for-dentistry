import { useQuery, type UseQueryResult } from '@tanstack/react-query'
import { api } from './api'
import type {
  Appointment,
  ClinicDentist,
  DuplicateReviewDetail,
  RefundabilityView,
  Encounter,
  TodayView,
  AttendanceDay,
  AttendanceSummary,
  Employee,
  ExpiringRow,
  BalanceSheet,
  CashFlowReport,
  GoodsReceipt,
  InventoryCategory,
  InventoryItem,
  LowStockRow,
  StockBalanceRow,
  StockBatch,
  StockCount,
  StockCountDetail,
  StockLocation,
  StockMovement,
  StockValuation,
  PayablesAgeingReport,
  PayablesAgeingRow,
  ProfitAndLoss,
  ReportCatalogue,
  PurchaseOrder,
  PurchaseOrderDetail,
  Supplier,
  SupplierBalance,
  SupplierInvoice,
  SupplierInvoiceDetail,
  SupplierPayment,
  UninvoicedLine,
  JobPosition,
  LinkableUser,
  ScheduleDay,
  TreatmentItem,
  TreatmentPlan,
  TreatmentPlanDetail,
  Account,
  AccountingPolicy,
  BankAccount,
  BankTransaction,
  BankTransactionDetail,
  AccountingPeriod,
  AccountStatement,
  AuditLogPage,
  CashRegister,
  CashSession,
  CashSessionDetail,
  ColumnMappingDefaults,
  Customer,
  CustomerBalance,
  Currency,
  ModuleInfo,
  Organization,
  OrganizationUser,
  PermissionCatalogEntry,
  PolicyRule,
  FiscalYear,
  JournalEntry,
  JournalEntryDetail,
  Invoice,
  InvoiceDetail,
  MatchCandidate,
  PatientDetail,
  PatientSummary,
  Payment,
  PaymentDetail,
  ReceivablesAgeing,
  Role,
  ServiceItem,
  SettlementHistoryRow,
  SettlementView,
  StatementImport,
  TrialBalance,
} from './types'

/** Query keys, centralised so an invalidation cannot miss a cache entry. */
export const queryKeys = {
  modules: ['platform', 'modules'] as const,
  permissionCatalogue: ['platform', 'permissions'] as const,
  organization: ['organization'] as const,
  currencies: ['organization', 'currencies'] as const,
  users: ['users'] as const,
  roles: ['roles'] as const,
  auditLogs: (filters: Record<string, unknown>) =>
    ['audit', 'logs', filters] as const,
  accounts: (filters: Record<string, unknown>) =>
    ['accounting', 'accounts', filters] as const,
  fiscalYears: ['accounting', 'fiscal-years'] as const,
  periods: ['accounting', 'periods'] as const,
  journal: (filters: Record<string, unknown>) =>
    ['accounting', 'journal', filters] as const,
  journalEntry: (id: string) => ['accounting', 'journal', id] as const,
  trialBalance: (from: string, to: string) =>
    ['accounting', 'trial-balance', from, to] as const,
  accountStatement: (id: string, from: string, to: string) =>
    ['accounting', 'statement', id, from, to] as const,
  policies: ['accounting', 'policies'] as const,
  policyRules: (id: string) => ['accounting', 'policy-rules', id] as const,
  customers: (filters: Record<string, unknown>) =>
    ['sales', 'customers', filters] as const,
  customerBalance: (id: string) => ['sales', 'customer-balance', id] as const,
  services: (filters: Record<string, unknown>) =>
    ['sales', 'services', filters] as const,
  invoices: (filters: Record<string, unknown>) =>
    ['sales', 'invoices', filters] as const,
  invoice: (id: string) => ['sales', 'invoice', id] as const,
  payments: (filters: Record<string, unknown>) =>
    ['sales', 'payments', filters] as const,
  payment: (id: string) => ['sales', 'payment', id] as const,
  ageing: (asOf: string) => ['sales', 'ageing', asOf] as const,
  appointments: (filters: Record<string, unknown>) =>
    ['clinic', 'appointments', filters] as const,
  clinicDentists: ['clinic', 'dentists'] as const,
  refundability: (paymentId: string) => ['sales', 'refundability', paymentId] as const,
  cashRegisters: ['cash', 'registers'] as const,
  cashSessions: (filters: Record<string, unknown>) =>
    ['cash', 'sessions', filters] as const,
  cashSession: (id: string) => ['cash', 'session', id] as const,
  bankAccounts: (includeInactive: boolean) =>
    ['banking', 'accounts', includeInactive] as const,
  columnMappingDefaults: ['banking', 'column-mapping', 'defaults'] as const,
  statementImports: (filters: Record<string, unknown>) =>
    ['banking', 'statements', filters] as const,
  bankTransactions: (filters: Record<string, unknown>) =>
    ['banking', 'transactions', filters] as const,
  bankTransaction: (id: string) => ['banking', 'transaction', id] as const,
  matchCandidates: (id: string) => ['banking', 'candidates', id] as const,
  patients: (filters: Record<string, unknown>) =>
    ['clinic', 'patients', filters] as const,
  patient: (id: string) => ['clinic', 'patient', id] as const,
  duplicateReviews: ['clinic', 'duplicate-reviews'] as const,
  today: ['clinic', 'today'] as const,
  encounters: (patientId: string) => ['clinic', 'encounters', patientId] as const,
  treatments: (patientId: string) => ['clinic', 'treatments', patientId] as const,
  suppliers: (filters: Record<string, unknown>) =>
    ['purchasing', 'suppliers', filters] as const,
  supplierBalance: (id: string) => ['purchasing', 'supplier', id, 'balance'] as const,
  purchaseOrders: (filters: Record<string, unknown>) =>
    ['purchasing', 'orders', filters] as const,
  purchaseOrder: (id: string) => ['purchasing', 'order', id] as const,
  goodsReceipts: (filters: Record<string, unknown>) =>
    ['purchasing', 'receipts', filters] as const,
  uninvoiced: (supplierId: string) =>
    ['purchasing', 'uninvoiced', supplierId] as const,
  bills: (filters: Record<string, unknown>) => ['purchasing', 'bills', filters] as const,
  bill: (id: string) => ['purchasing', 'bill', id] as const,
  supplierPayments: (filters: Record<string, unknown>) =>
    ['purchasing', 'payments', filters] as const,
  payablesAgeing: ['purchasing', 'payables', 'ageing'] as const,
  profitAndLoss: (filters: Record<string, unknown>) =>
    ['reporting', 'profit-and-loss', filters] as const,
  balanceSheet: (filters: Record<string, unknown>) =>
    ['reporting', 'balance-sheet', filters] as const,
  reportingReceivables: (asOf: string | null) =>
    ['reporting', 'receivables', asOf] as const,
  reportingPayables: (asOf: string | null) => ['reporting', 'payables', asOf] as const,
  cashFlow: (filters: Record<string, unknown>) => ['reporting', 'cash-flow', filters] as const,
  reportCatalogue: ['reporting', 'catalogue'] as const,
  inventoryItems: (filters: Record<string, unknown>) =>
    ['inventory', 'items', filters] as const,
  inventoryCategories: ['inventory', 'categories'] as const,
  stockLocations: ['inventory', 'locations'] as const,
  stockBatches: (itemId: string) => ['inventory', 'batches', itemId] as const,
  stockBalances: (filters: Record<string, unknown>) =>
    ['inventory', 'stock', filters] as const,
  lowStock: ['inventory', 'stock', 'low'] as const,
  expiringStock: (withinDays: number) => ['inventory', 'stock', 'expiring', withinDays] as const,
  stockMovements: (filters: Record<string, unknown>) =>
    ['inventory', 'movements', filters] as const,
  stockCounts: (filters: Record<string, unknown>) => ['inventory', 'counts', filters] as const,
  stockCount: (id: string) => ['inventory', 'count', id] as const,
  stockValuation: ['inventory', 'valuation'] as const,
  treatmentMaterials: (itemId: string) =>
    ['clinic', 'treatment', itemId, 'materials'] as const,
  employees: (filters: Record<string, unknown>) => ['hr', 'employees', filters] as const,
  employee: (id: string) => ['hr', 'employee', id] as const,
  positions: ['hr', 'positions'] as const,
  linkableUsers: ['hr', 'linkable-users'] as const,
  schedule: (employeeId: string) => ['hr', 'schedule', employeeId] as const,
  attendance: (filters: Record<string, unknown>) => ['hr', 'attendance', filters] as const,
  attendanceSummary: (filters: Record<string, unknown>) =>
    ['hr', 'attendance', 'summary', filters] as const,
  plans: (patientId: string) => ['clinic', 'plans', patientId] as const,
  plan: (planId: string) => ['clinic', 'plan', planId] as const,
  billable: (patientId: string) => ['clinic', 'billable', patientId] as const,
  settlement: (invoiceId: string) => ['clinic', 'settlement', invoiceId] as const,
  settlementHistory: (invoiceId: string) =>
    ['clinic', 'settlement', invoiceId, 'history'] as const,
}

export function useModules(): UseQueryResult<ModuleInfo[]> {
  return useQuery({
    queryKey: queryKeys.modules,
    queryFn: () => api.get<ModuleInfo[]>('/platform/modules'),
    // The registry changes only on deploy, so it does not need refetching.
    staleTime: 5 * 60_000,
  })
}

export function useOrganization(): UseQueryResult<Organization> {
  return useQuery({
    queryKey: queryKeys.organization,
    queryFn: () => api.get<Organization>('/organization'),
  })
}

export function useCurrencies(): UseQueryResult<Currency[]> {
  return useQuery({
    queryKey: queryKeys.currencies,
    queryFn: () => api.get<Currency[]>('/organization/currencies'),
    staleTime: 60 * 60_000,
  })
}

export function useOrganizationUsers(
  enabled: boolean,
): UseQueryResult<OrganizationUser[]> {
  return useQuery({
    queryKey: queryKeys.users,
    queryFn: () => api.get<OrganizationUser[]>('/users'),
    enabled,
  })
}

export function useRoles(enabled: boolean): UseQueryResult<Role[]> {
  return useQuery({
    queryKey: queryKeys.roles,
    queryFn: () => api.get<Role[]>('/roles'),
    enabled,
    staleTime: 5 * 60_000,
  })
}

export function usePermissionCatalogue(
  enabled: boolean,
): UseQueryResult<PermissionCatalogEntry[]> {
  return useQuery({
    queryKey: queryKeys.permissionCatalogue,
    queryFn: () => api.get<PermissionCatalogEntry[]>('/platform/permissions'),
    enabled,
    staleTime: 60 * 60_000,
  })
}

export function useAuditLogs(
  filters: { action?: string; outcome?: string; before?: string },
  enabled: boolean,
): UseQueryResult<AuditLogPage> {
  return useQuery({
    queryKey: queryKeys.auditLogs(filters),
    queryFn: () =>
      api.get<AuditLogPage>('/audit/logs', {
        action: filters.action,
        outcome: filters.outcome,
        before: filters.before,
        limit: 50,
      }),
    enabled,
  })
}


// --- accounting -------------------------------------------------------------

export function useAccounts(
  enabled: boolean,
  filters: { postable_only?: boolean; active_only?: boolean; search?: string } = {},
): UseQueryResult<Account[]> {
  return useQuery({
    queryKey: queryKeys.accounts(filters),
    queryFn: () => api.get<Account[]>('/accounting/accounts', filters),
    enabled,
  })
}

export function useFiscalYears(enabled: boolean): UseQueryResult<FiscalYear[]> {
  return useQuery({
    queryKey: queryKeys.fiscalYears,
    queryFn: () => api.get<FiscalYear[]>('/accounting/fiscal-years'),
    enabled,
  })
}

export function usePeriods(enabled: boolean): UseQueryResult<AccountingPeriod[]> {
  return useQuery({
    queryKey: queryKeys.periods,
    queryFn: () => api.get<AccountingPeriod[]>('/accounting/periods'),
    enabled,
  })
}

export function useJournal(
  enabled: boolean,
  filters: { entry_status?: string; from_date?: string; to_date?: string } = {},
): UseQueryResult<JournalEntry[]> {
  return useQuery({
    queryKey: queryKeys.journal(filters),
    queryFn: () => api.get<JournalEntry[]>('/accounting/journal', filters),
    enabled,
  })
}

export function useJournalEntry(
  id: string | null,
): UseQueryResult<JournalEntryDetail> {
  return useQuery({
    queryKey: queryKeys.journalEntry(id ?? ''),
    queryFn: () => api.get<JournalEntryDetail>(`/accounting/journal/${id}`),
    enabled: Boolean(id),
  })
}

export function useTrialBalance(
  enabled: boolean,
  from: string,
  to: string,
): UseQueryResult<TrialBalance> {
  return useQuery({
    queryKey: queryKeys.trialBalance(from, to),
    queryFn: () =>
      api.get<TrialBalance>('/accounting/reports/trial-balance', {
        from_date: from,
        to_date: to,
      }),
    enabled: enabled && Boolean(from && to),
  })
}

export function useAccountStatement(
  accountId: string | null,
  from: string,
  to: string,
): UseQueryResult<AccountStatement> {
  return useQuery({
    queryKey: queryKeys.accountStatement(accountId ?? '', from, to),
    queryFn: () =>
      api.get<AccountStatement>(
        `/accounting/reports/account-statement/${accountId}`,
        { from_date: from, to_date: to },
      ),
    enabled: Boolean(accountId && from && to),
  })
}


export function usePolicies(enabled: boolean): UseQueryResult<AccountingPolicy[]> {
  return useQuery({
    queryKey: queryKeys.policies,
    queryFn: () => api.get<AccountingPolicy[]>('/accounting/policies'),
    enabled,
  })
}

export function usePolicyRules(id: string | null): UseQueryResult<PolicyRule[]> {
  return useQuery({
    queryKey: queryKeys.policyRules(id ?? ''),
    queryFn: () => api.get<PolicyRule[]>(`/accounting/policies/${id}/rules`),
    enabled: Boolean(id),
  })
}


// --- sales ------------------------------------------------------------------

export function useCustomers(
  enabled: boolean,
  filters: { search?: string } = {},
): UseQueryResult<Customer[]> {
  return useQuery({
    queryKey: queryKeys.customers(filters),
    queryFn: () => api.get<Customer[]>('/sales/customers', filters),
    enabled,
  })
}

export function useCustomerBalance(
  customerId: string | null,
): UseQueryResult<CustomerBalance> {
  return useQuery({
    queryKey: queryKeys.customerBalance(customerId ?? ''),
    queryFn: () =>
      api.get<CustomerBalance>(`/sales/customers/${customerId}/balance`),
    enabled: Boolean(customerId),
  })
}

export function useServices(
  enabled: boolean,
  filters: { search?: string } = {},
): UseQueryResult<ServiceItem[]> {
  return useQuery({
    queryKey: queryKeys.services(filters),
    queryFn: () => api.get<ServiceItem[]>('/sales/services', filters),
    enabled,
  })
}

export function useInvoices(
  enabled: boolean,
  filters: { invoice_status?: string; customer_id?: string } = {},
): UseQueryResult<Invoice[]> {
  return useQuery({
    queryKey: queryKeys.invoices(filters),
    queryFn: () => api.get<Invoice[]>('/sales/invoices', filters),
    enabled,
  })
}

export function useInvoice(id: string | null): UseQueryResult<InvoiceDetail> {
  return useQuery({
    queryKey: queryKeys.invoice(id ?? ''),
    queryFn: () => api.get<InvoiceDetail>(`/sales/invoices/${id}`),
    enabled: Boolean(id),
  })
}

export function usePayments(
  enabled: boolean,
  filters: { customer_id?: string } = {},
): UseQueryResult<Payment[]> {
  return useQuery({
    queryKey: queryKeys.payments(filters),
    queryFn: () => api.get<Payment[]>('/sales/payments', filters),
    enabled,
  })
}

export function usePayment(id: string | null): UseQueryResult<PaymentDetail> {
  return useQuery({
    queryKey: queryKeys.payment(id ?? ''),
    queryFn: () => api.get<PaymentDetail>(`/sales/payments/${id}`),
    enabled: Boolean(id),
  })
}

export function useReceivablesAgeing(
  enabled: boolean,
  asOf: string,
): UseQueryResult<ReceivablesAgeing> {
  return useQuery({
    queryKey: queryKeys.ageing(asOf),
    queryFn: () =>
      api.get<ReceivablesAgeing>('/sales/receivables/ageing', { as_of: asOf }),
    enabled,
  })
}


/**
 * What of a payment may still be given back.
 *
 * Never cached: another cashier may have refunded something a minute ago, and
 * a stale figure would offer money that is no longer there.
 */
export function useRefundability(
  paymentId: string | null,
): UseQueryResult<RefundabilityView> {
  return useQuery({
    queryKey: queryKeys.refundability(paymentId ?? ''),
    queryFn: () =>
      api.get<RefundabilityView>(`/sales/payments/${paymentId}/refundability`),
    enabled: Boolean(paymentId),
    staleTime: 0,
    gcTime: 0,
  })
}


// --- cash -------------------------------------------------------------------

export function useCashRegisters(
  enabled: boolean,
): UseQueryResult<CashRegister[]> {
  return useQuery({
    queryKey: queryKeys.cashRegisters,
    queryFn: () => api.get<CashRegister[]>('/cash/registers'),
    enabled,
  })
}

export function useCashSessions(
  enabled: boolean,
  filters: { register_id?: string; session_status?: string } = {},
): UseQueryResult<CashSession[]> {
  return useQuery({
    queryKey: queryKeys.cashSessions(filters),
    queryFn: () => api.get<CashSession[]>('/cash/sessions', filters),
    enabled,
  })
}

export function useCashSession(
  id: string | null,
): UseQueryResult<CashSessionDetail> {
  return useQuery({
    queryKey: queryKeys.cashSession(id ?? ''),
    queryFn: () => api.get<CashSessionDetail>(`/cash/sessions/${id}`),
    enabled: Boolean(id),
  })
}


// --- banking ----------------------------------------------------------------

export function useBankAccounts(
  enabled: boolean,
  includeInactive = false,
): UseQueryResult<BankAccount[]> {
  return useQuery({
    queryKey: queryKeys.bankAccounts(includeInactive),
    queryFn: () =>
      api.get<BankAccount[]>('/banking/accounts', {
        include_inactive: includeInactive,
      }),
    enabled,
  })
}

export function useColumnMappingDefaults(
  enabled: boolean,
): UseQueryResult<ColumnMappingDefaults> {
  return useQuery({
    queryKey: queryKeys.columnMappingDefaults,
    queryFn: () =>
      api.get<ColumnMappingDefaults>('/banking/column-mapping/defaults'),
    enabled,
    // The aliases are compiled into the build, so they never go stale.
    staleTime: 60 * 60_000,
  })
}

export function useStatementImports(
  enabled: boolean,
  filters: { bank_account_id?: string } = {},
): UseQueryResult<StatementImport[]> {
  return useQuery({
    queryKey: queryKeys.statementImports(filters),
    queryFn: () => api.get<StatementImport[]>('/banking/statements', filters),
    enabled,
  })
}

export function useBankTransactions(
  enabled: boolean,
  filters: { bank_account_id?: string; transaction_status?: string } = {},
): UseQueryResult<BankTransaction[]> {
  return useQuery({
    queryKey: queryKeys.bankTransactions(filters),
    queryFn: () => api.get<BankTransaction[]>('/banking/transactions', filters),
    enabled,
  })
}

export function useBankTransaction(
  id: string | null,
): UseQueryResult<BankTransactionDetail> {
  return useQuery({
    queryKey: queryKeys.bankTransaction(id ?? ''),
    queryFn: () => api.get<BankTransactionDetail>(`/banking/transactions/${id}`),
    enabled: Boolean(id),
  })
}

export function useMatchCandidates(
  id: string | null,
): UseQueryResult<MatchCandidate[]> {
  return useQuery({
    queryKey: queryKeys.matchCandidates(id ?? ''),
    queryFn: () =>
      api.get<MatchCandidate[]>(`/banking/transactions/${id}/candidates`),
    enabled: Boolean(id),
  })
}


// --- clinic -----------------------------------------------------------------

export function usePatients(
  enabled: boolean,
  filters: { query?: string } = {},
): UseQueryResult<PatientSummary[]> {
  return useQuery({
    queryKey: queryKeys.patients(filters),
    queryFn: () => api.get<PatientSummary[]>('/clinic/patients', filters),
    enabled,
  })
}

/**
 * The clinic's day. Refetched on focus, because a dashboard somebody left
 * open over lunch should not still be showing the morning's takings.
 */
export function useToday(): UseQueryResult<TodayView> {
  return useQuery({
    queryKey: queryKeys.today,
    queryFn: () => api.get<TodayView>('/clinic/today'),
    staleTime: 30_000,
    refetchOnWindowFocus: true,
  })
}

/**
 * The queue of suspected duplicates, both records attached so they can be
 * compared without a second round trip per row.
 */
export function useDuplicateReviews(
  enabled: boolean,
): UseQueryResult<DuplicateReviewDetail[]> {
  return useQuery({
    queryKey: queryKeys.duplicateReviews,
    queryFn: () =>
      api.get<DuplicateReviewDetail[]>('/clinic/duplicate-reviews'),
    enabled,
  })
}

/**
 * The day's bookings, or one patient's history of them.
 *
 * Short-lived: in a clinic of three the same screen is open on two machines,
 * and a slot somebody has just taken must not still look free.
 */
export function useAppointments(
  enabled: boolean,
  filters: { on?: string; patient_id?: string } = {},
): UseQueryResult<Appointment[]> {
  return useQuery({
    queryKey: queryKeys.appointments(filters),
    queryFn: () => api.get<Appointment[]>('/clinic/appointments', filters),
    enabled,
    staleTime: 10_000,
    refetchOnWindowFocus: true,
  })
}


/**
 * The people a booking may be given to.
 *
 * Not `useOrganizationUsers`: that needs `identity.user.read`, which
 * reception does not hold, and the form that could not name a dentist was
 * the form nobody could book with. Behind `clinic.appointment.read`, like
 * the day it is drawn beside. The clinic has three of these; it rarely
 * changes, so it is cached for the session.
 */
export function useClinicDentists(enabled: boolean): UseQueryResult<ClinicDentist[]> {
  return useQuery({
    queryKey: queryKeys.clinicDentists,
    queryFn: () => api.get<ClinicDentist[]>('/clinic/dentists'),
    enabled,
    staleTime: 300_000,
  })
}

/** A patient's visits, newest first. Behind `clinic.clinical.read`. */
export function useEncounters(
  patientId: string | null,
  enabled: boolean,
): UseQueryResult<Encounter[]> {
  return useQuery({
    queryKey: queryKeys.encounters(patientId ?? ''),
    queryFn: () => api.get<Encounter[]>(`/clinic/patients/${patientId}/encounters`),
    enabled: Boolean(patientId) && enabled,
  })
}

/** Every piece of work recorded for a patient, whatever its state. */
export function useTreatments(
  patientId: string | null,
  enabled: boolean,
): UseQueryResult<TreatmentItem[]> {
  return useQuery({
    queryKey: queryKeys.treatments(patientId ?? ''),
    queryFn: () => api.get<TreatmentItem[]>(`/clinic/patients/${patientId}/treatments`),
    enabled: Boolean(patientId) && enabled,
  })
}

/** The courses of treatment on a patient's record, newest first. */
export function usePlans(
  patientId: string | null,
  enabled: boolean,
): UseQueryResult<TreatmentPlan[]> {
  return useQuery({
    queryKey: queryKeys.plans(patientId ?? ''),
    queryFn: () =>
      api.get<TreatmentPlan[]>('/clinic/plans', { patient_id: patientId ?? '' }),
    enabled: Boolean(patientId) && enabled,
  })
}

/**
 * One plan with the work recorded against it.
 *
 * Fetched only when a plan is actually opened: reading it writes an audit row
 * naming who looked at whose clinical record, so it is not something to do
 * speculatively for every row in a list.
 */
export function usePlan(
  planId: string | null,
  enabled: boolean,
): UseQueryResult<TreatmentPlanDetail> {
  return useQuery({
    queryKey: queryKeys.plan(planId ?? ''),
    queryFn: () => api.get<TreatmentPlanDetail>(`/clinic/plans/${planId}`),
    enabled: Boolean(planId) && enabled,
  })
}

/**
 * Completed work with no invoice line: what the clinic has done and not
 * charged for. Never cached beyond the moment — billing it is what makes it
 * disappear, and a stale list would offer to bill the same tooth twice.
 */
export function useBillable(
  patientId: string | null,
  enabled: boolean,
): UseQueryResult<TreatmentItem[]> {
  return useQuery({
    queryKey: queryKeys.billable(patientId ?? ''),
    queryFn: () => api.get<TreatmentItem[]>(`/clinic/patients/${patientId}/billable`),
    enabled: Boolean(patientId) && enabled,
    staleTime: 0,
    gcTime: 0,
  })
}

export function usePatient(
  id: string | null,
): UseQueryResult<PatientDetail> {
  return useQuery({
    queryKey: queryKeys.patient(id ?? ''),
    queryFn: () => api.get<PatientDetail>(`/clinic/patients/${id}`),
    enabled: Boolean(id),
  })
}

/**
 * The remaining balance and everything around it.
 *
 * Never cached beyond the moment: a stale balance is exactly the figure the
 * panel must not show, and another cashier may have taken money a second ago.
 */
export function useSettlement(
  invoiceId: string | null,
): UseQueryResult<SettlementView> {
  return useQuery({
    queryKey: queryKeys.settlement(invoiceId ?? ''),
    queryFn: () =>
      api.get<SettlementView>(`/clinic/invoices/${invoiceId}/settlement`),
    enabled: Boolean(invoiceId),
    staleTime: 0,
    gcTime: 0,
  })
}

export function useSettlementHistory(
  invoiceId: string | null,
): UseQueryResult<SettlementHistoryRow[]> {
  return useQuery({
    queryKey: queryKeys.settlementHistory(invoiceId ?? ''),
    queryFn: () =>
      api.get<SettlementHistoryRow[]>(
        `/clinic/invoices/${invoiceId}/settlement/history`,
      ),
    enabled: Boolean(invoiceId),
    staleTime: 0,
  })
}


// --- employees --------------------------------------------------------------

export function useEmployees(
  filters: { q?: string; status?: string },
  enabled: boolean,
): UseQueryResult<Employee[]> {
  return useQuery({
    queryKey: queryKeys.employees(filters),
    queryFn: () => api.get<Employee[]>('/hr/employees', filters),
    enabled,
  })
}

export function useEmployee(
  employeeId: string | null,
  enabled: boolean,
): UseQueryResult<Employee> {
  return useQuery({
    queryKey: queryKeys.employee(employeeId ?? ''),
    queryFn: () => api.get<Employee>(`/hr/employees/${employeeId}`),
    enabled: Boolean(employeeId) && enabled,
  })
}

export function usePositions(enabled: boolean): UseQueryResult<JobPosition[]> {
  return useQuery({
    queryKey: queryKeys.positions,
    queryFn: () => api.get<JobPosition[]>('/hr/positions'),
    enabled,
  })
}

/**
 * Members with no employee record yet.
 *
 * Behind `hr.employee.manage`, so it is requested only for somebody who may
 * actually link one — asking for it as a reader produces a 403 the screen
 * would then have to explain away.
 */
export function useLinkableUsers(enabled: boolean): UseQueryResult<LinkableUser[]> {
  return useQuery({
    queryKey: queryKeys.linkableUsers,
    queryFn: () => api.get<LinkableUser[]>('/hr/employees/linkable-users'),
    enabled,
  })
}

export function useSchedule(
  employeeId: string | null,
  enabled: boolean,
): UseQueryResult<ScheduleDay[]> {
  return useQuery({
    queryKey: queryKeys.schedule(employeeId ?? ''),
    queryFn: () => api.get<ScheduleDay[]>(`/hr/employees/${employeeId}/schedule`),
    enabled: Boolean(employeeId) && enabled,
  })
}

export function useAttendance(
  filters: { employee_id?: string; from_date?: string; to_date?: string },
  enabled: boolean,
): UseQueryResult<AttendanceDay[]> {
  return useQuery({
    queryKey: queryKeys.attendance(filters),
    queryFn: () => api.get<AttendanceDay[]>('/hr/attendance', filters),
    enabled,
  })
}

export function useAttendanceSummary(
  filters: { employee_id: string; from_date: string; to_date: string },
  enabled: boolean,
): UseQueryResult<AttendanceSummary> {
  return useQuery({
    queryKey: queryKeys.attendanceSummary(filters),
    queryFn: () => api.get<AttendanceSummary>('/hr/attendance/summary', filters),
    enabled,
  })
}


// --- purchasing -------------------------------------------------------------

export function useSuppliers(
  filters: { q?: string; include_inactive?: boolean },
  enabled: boolean,
): UseQueryResult<Supplier[]> {
  return useQuery({
    queryKey: queryKeys.suppliers(filters),
    queryFn: () => api.get<Supplier[]>('/purchasing/suppliers', filters),
    enabled,
  })
}

export function useSupplierBalance(
  supplierId: string | null,
  enabled: boolean,
): UseQueryResult<SupplierBalance> {
  return useQuery({
    queryKey: queryKeys.supplierBalance(supplierId ?? ''),
    queryFn: () =>
      api.get<SupplierBalance>(`/purchasing/suppliers/${supplierId}/balance`),
    enabled: Boolean(supplierId) && enabled,
  })
}

export function usePurchaseOrders(
  filters: { supplier_id?: string; status?: string },
  enabled: boolean,
): UseQueryResult<PurchaseOrder[]> {
  return useQuery({
    queryKey: queryKeys.purchaseOrders(filters),
    queryFn: () => api.get<PurchaseOrder[]>('/purchasing/orders', filters),
    enabled,
  })
}

export function usePurchaseOrder(
  orderId: string | null,
  enabled: boolean,
): UseQueryResult<PurchaseOrderDetail> {
  return useQuery({
    queryKey: queryKeys.purchaseOrder(orderId ?? ''),
    queryFn: () => api.get<PurchaseOrderDetail>(`/purchasing/orders/${orderId}`),
    enabled: Boolean(orderId) && enabled,
  })
}

export function useGoodsReceipts(
  filters: { supplier_id?: string; order_id?: string },
  enabled: boolean,
): UseQueryResult<GoodsReceipt[]> {
  return useQuery({
    queryKey: queryKeys.goodsReceipts(filters),
    queryFn: () => api.get<GoodsReceipt[]>('/purchasing/receipts', filters),
    enabled,
  })
}

/**
 * What has arrived from a supplier and nobody has billed for.
 *
 * Never cached: billing a line is what makes it disappear, and a stale list
 * would offer to invoice the same delivery twice — which is the exact
 * failure the whole four-record shape exists to prevent.
 */
export function useUninvoicedLines(
  supplierId: string | null,
  enabled: boolean,
): UseQueryResult<UninvoicedLine[]> {
  return useQuery({
    queryKey: queryKeys.uninvoiced(supplierId ?? ''),
    queryFn: () =>
      api.get<UninvoicedLine[]>(`/purchasing/suppliers/${supplierId}/uninvoiced`),
    enabled: Boolean(supplierId) && enabled,
    staleTime: 0,
    gcTime: 0,
  })
}

export function useBills(
  filters: { supplier_id?: string; status?: string },
  enabled: boolean,
): UseQueryResult<SupplierInvoice[]> {
  return useQuery({
    queryKey: queryKeys.bills(filters),
    queryFn: () => api.get<SupplierInvoice[]>('/purchasing/bills', filters),
    enabled,
  })
}

export function useBill(
  invoiceId: string | null,
  enabled: boolean,
): UseQueryResult<SupplierInvoiceDetail> {
  return useQuery({
    queryKey: queryKeys.bill(invoiceId ?? ''),
    queryFn: () => api.get<SupplierInvoiceDetail>(`/purchasing/bills/${invoiceId}`),
    enabled: Boolean(invoiceId) && enabled,
  })
}

export function useSupplierPayments(
  filters: { supplier_id?: string },
  enabled: boolean,
): UseQueryResult<SupplierPayment[]> {
  return useQuery({
    queryKey: queryKeys.supplierPayments(filters),
    queryFn: () => api.get<SupplierPayment[]>('/purchasing/payments', filters),
    enabled,
  })
}

export function usePayablesAgeing(
  enabled: boolean,
): UseQueryResult<PayablesAgeingRow[]> {
  return useQuery({
    queryKey: queryKeys.payablesAgeing,
    queryFn: () => api.get<PayablesAgeingRow[]>('/purchasing/payables/ageing'),
    enabled,
  })
}

// --- inventory --------------------------------------------------------------

export function useInventoryItems(
  filters: { search?: string; category_id?: string; include_inactive?: boolean },
  enabled: boolean,
): UseQueryResult<InventoryItem[]> {
  return useQuery({
    queryKey: queryKeys.inventoryItems(filters),
    queryFn: () => api.get<InventoryItem[]>('/inventory/items', filters),
    enabled,
  })
}

export function useInventoryCategories(enabled: boolean): UseQueryResult<InventoryCategory[]> {
  return useQuery({
    queryKey: queryKeys.inventoryCategories,
    queryFn: () => api.get<InventoryCategory[]>('/inventory/categories'),
    enabled,
  })
}

export function useStockLocations(enabled: boolean): UseQueryResult<StockLocation[]> {
  return useQuery({
    queryKey: queryKeys.stockLocations,
    queryFn: () => api.get<StockLocation[]>('/inventory/locations'),
    enabled,
  })
}

export function useStockBatches(
  itemId: string | null,
  enabled: boolean,
): UseQueryResult<StockBatch[]> {
  return useQuery({
    queryKey: queryKeys.stockBatches(itemId ?? ''),
    queryFn: () => api.get<StockBatch[]>('/inventory/batches', { item_id: itemId ?? undefined }),
    enabled: Boolean(itemId) && enabled,
  })
}

/**
 * What is on the shelves.
 *
 * Never cached beyond the moment it is shown: a stale balance is how
 * somebody promises a material that another surgery has already used.
 */
export function useStockBalances(
  filters: { item_id?: string; location_id?: string },
  enabled: boolean,
): UseQueryResult<StockBalanceRow[]> {
  return useQuery({
    queryKey: queryKeys.stockBalances(filters),
    queryFn: () => api.get<StockBalanceRow[]>('/inventory/stock', filters),
    enabled,
    staleTime: 0,
  })
}

export function useLowStock(enabled: boolean): UseQueryResult<LowStockRow[]> {
  return useQuery({
    queryKey: queryKeys.lowStock,
    queryFn: () => api.get<LowStockRow[]>('/inventory/stock/low'),
    enabled,
    staleTime: 0,
  })
}

export function useExpiringStock(
  withinDays: number,
  enabled: boolean,
): UseQueryResult<ExpiringRow[]> {
  return useQuery({
    queryKey: queryKeys.expiringStock(withinDays),
    queryFn: () => api.get<ExpiringRow[]>('/inventory/stock/expiring', { within_days: withinDays }),
    enabled,
    staleTime: 0,
  })
}

export function useStockMovements(
  filters: { item_id?: string; location_id?: string; kind?: string; limit?: number },
  enabled: boolean,
): UseQueryResult<StockMovement[]> {
  return useQuery({
    queryKey: queryKeys.stockMovements(filters),
    queryFn: () => api.get<StockMovement[]>('/inventory/movements', filters),
    enabled,
  })
}

export function useStockCounts(
  filters: { status?: string },
  enabled: boolean,
): UseQueryResult<StockCount[]> {
  return useQuery({
    queryKey: queryKeys.stockCounts(filters),
    queryFn: () => api.get<StockCount[]>('/inventory/counts', filters),
    enabled,
  })
}

export function useStockCount(
  countId: string | null,
  enabled: boolean,
): UseQueryResult<StockCountDetail> {
  return useQuery({
    queryKey: queryKeys.stockCount(countId ?? ''),
    queryFn: () => api.get<StockCountDetail>(`/inventory/counts/${countId}`),
    enabled: Boolean(countId) && enabled,
  })
}

export function useStockValuation(enabled: boolean): UseQueryResult<StockValuation> {
  return useQuery({
    queryKey: queryKeys.stockValuation,
    queryFn: () => api.get<StockValuation>('/inventory/valuation'),
    enabled,
  })
}

export function useTreatmentMaterials(
  treatmentItemId: string | null,
  enabled: boolean,
): UseQueryResult<StockMovement[]> {
  return useQuery({
    queryKey: queryKeys.treatmentMaterials(treatmentItemId ?? ''),
    queryFn: () =>
      api.get<StockMovement[]>(`/clinic/treatments/${treatmentItemId}/materials`),
    enabled: Boolean(treatmentItemId) && enabled,
  })
}

// --- reporting --------------------------------------------------------------

/**
 * What this build can actually report, answered by the backend.
 *
 * The screen renders this rather than a list written here, so a report can
 * never be offered that does not exist, and a blocked statutory form always
 * names the question that blocks it.
 */
export function useReportCatalogue(enabled: boolean): UseQueryResult<ReportCatalogue> {
  return useQuery({
    queryKey: queryKeys.reportCatalogue,
    queryFn: () => api.get<ReportCatalogue>('/reporting/catalogue'),
    enabled,
  })
}

export function useProfitAndLoss(
  filters: { from_date: string; to_date: string; compare_from?: string; compare_to?: string },
  enabled: boolean,
): UseQueryResult<ProfitAndLoss> {
  return useQuery({
    queryKey: queryKeys.profitAndLoss(filters),
    queryFn: () => api.get<ProfitAndLoss>('/reporting/profit-and-loss', filters),
    enabled,
  })
}

export function useBalanceSheet(
  filters: { as_of: string; compare_as_of?: string },
  enabled: boolean,
): UseQueryResult<BalanceSheet> {
  return useQuery({
    queryKey: queryKeys.balanceSheet(filters),
    queryFn: () => api.get<BalanceSheet>('/reporting/balance-sheet', filters),
    enabled,
  })
}

export function useReportingReceivables(
  asOf: string | null,
  enabled: boolean,
): UseQueryResult<ReceivablesAgeing> {
  return useQuery({
    queryKey: queryKeys.reportingReceivables(asOf),
    queryFn: () =>
      api.get<ReceivablesAgeing>('/reporting/receivables-ageing', {
        as_of: asOf ?? undefined,
      }),
    enabled,
  })
}

export function useReportingPayables(
  asOf: string | null,
  enabled: boolean,
): UseQueryResult<PayablesAgeingReport> {
  return useQuery({
    queryKey: queryKeys.reportingPayables(asOf),
    queryFn: () =>
      api.get<PayablesAgeingReport>('/reporting/payables-ageing', {
        as_of: asOf ?? undefined,
      }),
    enabled,
  })
}

export function useCashFlow(
  filters: { from_date: string; to_date: string },
  enabled: boolean,
): UseQueryResult<CashFlowReport> {
  return useQuery({
    queryKey: queryKeys.cashFlow(filters),
    queryFn: () => api.get<CashFlowReport>('/reporting/cash-flow', filters),
    enabled,
  })
}
