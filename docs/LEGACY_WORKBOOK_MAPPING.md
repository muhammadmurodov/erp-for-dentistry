# Legacy workbook analysis and mapping

What the clinic's existing Excel workbook contains, what it reveals about
current practice, and where each concept lands in the new system.

**Method.** The workbook was read with `openpyxl` in read-only mode. Its
macros were never executed and the file was never modified. Analysis ran
against a copy in a scratch directory.

**Privacy.** The workbook contains the clinic's bank account numbers, its
director's name, employee salary history and patient-level fiscal receipts.
None of that appears in this document, in any test fixture, or anywhere in
version control. Only structure, column semantics and accounting rules are
recorded here. Amounts quoted below are aggregates used to size the problem,
never individual records.

## Sheets

23 sheets. Grouped by what they are:

| Sheet | Rows | What it is |
| --- | --- | --- |
| `Маълумотлар` | 77 | Company registration details, plus lookup tables for account level and debit/credit nature |
| `hisobraqam` | 402 | The Uzbek national chart of accounts, as a hierarchy |
| `Учетная_политика` | 102 | **Accounting policy: 77 operation types → debit/credit account pairs** |
| `Бош_китоб` | 59 | The general ledger (NSBU standard 20, appendix 2) |
| `Balance` | 120 | Statutory balance sheet, Form No. 1, with line codes |
| `BankAylanmasi` | 2 345 | Bank statement lines, 2017 onward, with classification and reconciliation columns |
| `ПлатежкиБанк_UZS` | 534 | Outgoing payment orders |
| `bank` | 1 129 | Directory of Uzbek bank branch codes (MFO) |
| `Kassa 2025`, `Kassa 2026`, `Kassa2024`, `Kassa2023` | 963 / 88 / 1 134 / 1 072 | Fiscal receipt exports, one row per receipt line |
| `Kassa` | 1 336 | Earlier cash/card receipt register (2021–) |
| `Иш_ҳаққи` | 217 | Payroll calculation, monthly per employee |
| `tabel` | 27 | Statutory timesheet attendance codes |
| `Пенсия_справкаси`, `Иш_ҳақи_справкаси-*` | 54 / 43 | Pension and salary certificates for individuals |
| `Ишончнома` | 3 | Power-of-attorney template |
| `2021-hisobotlari` | 11 | Tax reconciliation worksheet for 2021 |
| `*-Pivot`, `kassa2024-pivot` | — | Pivot summaries over the above |

### Confirmed facts

- The entity is a dental clinic, an MChJ, registered 2017, in Bukhara region.
- It banks with one commercial bank; several accounts exist, including transit
  accounts for card settlement and for payroll.
- Patients pay cash and by bank card. In 2025 cash was roughly 96% of receipts
  by value, card roughly 4%.
- Fiscal receipts are produced by a registered fiscal module and exported with
  ИКПУ (MXIK) product codes — a statutory requirement.
- The books follow the Uzbek national chart of accounts.
- Bookkeeping is partly manual: the general ledger sheet has 47 populated rows
  for a business with thousands of transactions, so it summarises rather than
  records each event.

### Not confirmed

Whether partial or deferred patient payments are accepted. The receipt data
shows one payment per receipt and carries no receivable, which is consistent
with payment on completion — but absence of evidence is not evidence. See
`OPEN_QUESTIONS.md` question S1.

## The accounting policy sheet

This is the most valuable find. `Учетная_политика` maps an operation type to
a debit and a credit account:

| Operation (translated) | Dr | Cr |
| --- | --- | --- |
| Charter capital subscribed | 4610 | 8330 |
| Equipment contributed to charter capital | 0130 | 4610 |
| Cash service rendered | 5010 | 9030 |
| Cash deposited to bank via collection | 5710.02 | 5010 |
| Transit to main account (collection) | 5110.01 | 5710.02 |
| Card service rendered — online | 5110.03 | 9030 |
| Transit to main account (online card) | 5110.01 | 5110.03 |
| Card service rendered — HUMO | 5110.04 | 9030 |
| Advance to supplier for materials | 4310 | 5110.01 |
| Materials received from supplier | 1010 | 6010 |
| Supplier advance cleared on invoice | 6010 | 4310 |
| Wages accrued — core activity | 2010 | 6710 |
| Social tax accrued — core activity | 2010 | 6520.01 |
| Income tax withheld from wages | 6710 | 6411 |
| Wages paid via payroll transit account | 6710 | 5111.05 |
| Depreciation charged | 2010 | 0200 |
| Materials consumed in core activity | 2010 | 1010 |
| Core activity cost to cost of sales | 9130 | 2010 |
| Period close — cost of sales | 9910 | 9130 |
| Period close — admin expenses | 9910 | 9420 |
| Period close — revenue | 9030 | 9910 |
| Year close — profit | 9910 | 8710 |

This is a `TransactionClassificationRule` table, and the new system implements
the same idea with the properties the sheet lacks: per-organization,
versioned, effective-dated, validated before activation, restricted to
authorised users, and audited.

### Three defects in the sheet, and what they imply

**1. A policy edit silently rewrites history.** The Dr/Cr columns in both the
general ledger and the bank statement sheet are
`INDEX(Учетная_политика!…, MATCH(operation_type, …))` formulas. They are not
stored values. Change the policy row and every historical entry that used that
operation type now *appears* to have been posted differently. The books are
retroactively altered by a single cell edit, with no trace.

This is why the new engine stores the **resolved account ids** and the policy
version on each posted entry. It is the single most important lesson the
workbook teaches.

**2. Two rows are flagged as needing review by the author.** Rows 82 and 83
are marked "Қайта кўриб чиқиш керак" ("needs re-examination"): payments for
services without an invoice, posted to 9430 and 9420. The author was unsure.
Carried to `OPEN_QUESTIONS.md` question A4 rather than copied.

**3. Three rows are self-referential.** Rows 77–79 map an operation to the
same account on both sides (9030/9030, 9910/9910). A debit and credit to one
account is a no-op. These are period-close scaffolding rather than real rules,
and are not carried over.

## Bank statement and reconciliation

`BankAylanmasi` has 36 columns. The interesting ones:

| Column (translated) | Role |
| --- | --- |
| Sequence number | The key fiscal receipts reference back to |
| Date, inflow, outflow, opening, closing | The statement itself |
| Document number, payment purpose, counterparty name/account/MFO | Statement detail |
| Operation code, operation type, category | Classification |
| Dr account, Cr account | Derived from the policy sheet by lookup |
| "Operation closed" flag | Whether it has been posted |
| Bank commission %, net receipt | Commission handling |
| Cash report amount: cash / card / total / **difference** | Reconciliation |

### How the clinic reconciles today

The pattern is a genuine many-to-one match, and it is worth stating precisely
because it drives the Phase 3 design:

1. Each bank statement line gets a sequence number.
2. Each fiscal receipt row records the bank line it was settled by.
3. The bank sheet sums receipts per bank line
   (`SUMIF(receipts[bank_line_ref], this_row_number, …)`).
4. A difference column compares that sum against the bank amount.

So the clinic already works with **many receipts → one settlement**, and
already measures the discrepancy. The new system must support one-to-one,
one-to-many and many-to-one matches for the same reason.

### Commission handling

The sheet computes `gross = net / (1 − commission%)`. This recovers the right
revenue figure, but:

- the commission is never recorded as an expense, so operating costs are
  understated;
- it cannot represent one settlement covering several payments at different
  rates.

The new system records the gross payment, the settlement and the fee as
separate lines of one entry. See `ACCOUNTING_DESIGN.md`.

### The five stages, and why they are distinct

The workbook's data makes clear that one economic transaction appears as up to
five records: patient payment, fiscal receipt, card-processor settlement, bank
statement line, accounting entry. Counting each as revenue would multiply
turnover severalfold. The new system models these as distinct entities with
explicit links, and revenue is recognised at exactly one of them.

## Fiscal receipts

`Kassa 2025` and `Kassa 2026` use the current fiscal-module export format:

| Column | Meaning |
| --- | --- |
| Product ID | `UZ` + fiscal module serial + receipt number |
| STIR/JSHSHIR, FM number | Taxpayer and fiscal module identifiers |
| Receipt date-time, receipt number | When and which |
| Product name, quantity, price | The service sold |
| Discount, loyalty discount, VAT amount | Adjustments |
| Total cash, total bank card, total VAT | How it was paid |
| ИКПУ (MXIK) code, unit code, barcode | Statutory product classification |
| Receipt type | `Сотув` (sale) |

This is the import format the Phase 3 fiscal-receipt importer targets.

### A VAT contradiction

Every receipt reports **zero VAT**, yet the workbook adds a check column
computing `ROUNDUP(price / 1.12, …)` — a 12% extraction — and the 2025 bank
statement shows VAT payments of roughly 2.9 million UZS alongside profit-tax
payments. Meanwhile the 2021 worksheet computes tax as 4% of turnover.

So the tax regime changed between 2021 and 2025, and the receipt data and the
bank data disagree about whether these services bear VAT. This is the most
consequential unresolved question in the analysis and is **not** guessed
anywhere in the code: `OPEN_QUESTIONS.md` question A5.

## Payroll

`Иш_ҳаққи` computes, per employee per month: salary, days worked against days
in the month, cumulative year income, income tax, INPS (individual pension
accumulation), SVGPF at 8%, social tax at 12%/15% with a minimum base of 65%
of the 2018 minimum wage, and net pay. Income tax comes from a VBA function
named for the year 2018.

**None of these rates is carried into the new system.** They are historical,
the VBA was not executed, and payroll rules in Uzbekistan change by decree.
Phase 5 implements effective-dated, versioned statutory rule sets whose values
must be supplied and signed off by the accountant:
`OPEN_QUESTIONS.md` question P2.

What *is* reusable is the shape: proration by days worked, cumulative
year-to-date income driving a progressive calculation, and a minimum
contribution base. And the `tabel` sheet's attendance codes are statutory
reference data, extracted to
`backend/app/reference/data/uz_timesheet_codes.json`.

> That extraction surfaced a gap: 12 of the 25 codes have Russian text only,
> because the workbook's author stopped filling the Uzbek column. The Uzbek
> wording is statutory and is **not** invented — the gap is recorded in the
> data file and a test fails if it changes without being re-declared.

## Chart of accounts

`hisobraqam` holds 396 rows of the national chart, with a level column (part /
section / group / account / sub-account / synthetic), a debit-credit nature
column (active / passive / contra-active / contra-passive / transit /
off-balance), and a "needed" flag marking which accounts the author considered
relevant to a small business.

368 rows were extracted to
`backend/app/reference/data/uz_chart_of_accounts.json` (28 rows were
header/`#N/A` artefacts).

### Two defects found

**1. The hierarchy column is incomplete.** `ParentID` is filled for part I and
section II only. Sections III through IX have no parent, so walking the parent
chain resolves the statutory part for a minority of rows.

**2. One parent reference is miscased** — `ACC-II-Қисм` against an actual id
of `ACC-II-ҚИСМ`.

So the statutory part and accounting element are **derived from the account
code range**, which is how the chart is actually organised, and every derived
field is flagged `needs_confirmation`. The derivation was cross-checked
against the twelve accounts the policy sheet actually posts to (5010, 5110,
5710, 6010, 6710, 7810, 8710, 9030, 9130, 9420, 9610, 9910) and agrees with
observed usage in every case; that cross-check is a test, so a future edit to
the reference file cannot silently reclassify the P&L.

## Concept mapping

| Workbook concept | New system |
| --- | --- |
| `Маълумотлар` company block | `Organization` |
| `Маълумотлар` lookup tables | `Account.level`, `Account.nature` enums |
| `hisobraqam` | `Account` hierarchy, seeded from reference data |
| `Учетная_политика` | `AccountingPolicy` + `PolicyRule`, versioned and effective-dated |
| `Бош_китоб` | `JournalEntry` + `JournalLine` (n lines, not 2) |
| `Бош_китоб` "operation closed" flag | `JournalEntry.status` = `POSTED` |
| `Balance` Form No. 1 | `ReportLineDefinition` (Phase 6) |
| `BankAylanmasi` | `BankTransaction` + `BankStatementImport` |
| `BankAylanmasi` receipt-reference columns | `ReconciliationMatch` (many-to-one capable) |
| `BankAylanmasi` commission columns | Explicit fee line on the settlement entry |
| `ПлатежкиБанк_UZS` | `PaymentOrder` |
| `bank` MFO directory | Counterparty bank validation reference |
| `Kassa 20xx` | `FiscalReceipt` + `FiscalReceiptLine` |
| `Kassa` (2021 register) | Historical import only |
| `Иш_ҳаққи` | `PayrollRun`, `PayrollLine`, `StatutoryRuleVersion` |
| `tabel` | `AttendanceCode` reference data |
| Pension / salary certificates | `Document` generation (Phase 7) |
| `2021-hisobotlari` | Superseded by tax reporting (Phase 6) |
| Pivot sheets | Superseded by reports |

## Migration stance

Historical data is **not** imported in Phases 1–5. Reasons:

- The opening balances it would produce depend on classification decisions
  that are still open.
- Formula-derived Dr/Cr columns mean the historical postings are not stable
  values; they are whatever the current policy sheet says.
- The physical ledger that complements the workbook is unavailable, so the
  workbook is known to be incomplete.

The intended path is to open the new books with an **opening balance journal
entry** at a cut-over date, signed off by the accountant, with the workbook
retained as the historical record. Importing the fiscal receipt history for
analysis is separable and low-risk, and is a candidate once the VAT question
is answered.
