import { useQuery, type UseQueryResult } from '@tanstack/react-query'
import { api } from './api'
import type {
  DuplicateReviewDetail,
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
