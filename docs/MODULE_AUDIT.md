# Module audit — the thirteen sidebar entries

**Produced:** 2026-11-03, by reading the repository rather than its documentation.
Where this disagrees with `IMPLEMENTATION_STATUS.md` or the module registry, the
code is what was believed.

This is the input to the completion programme. Each module below lists what is
built, what is missing on each layer, and what it is blocked on. The order of
work at the end follows from the dependencies, not from the sidebar.

## How the audit was done

- Every router enumerated (`app/api/routers/*.py`) and matched against its service.
- Every model matched against a migration (0001–0012, head `0012`).
- Every frontend route in `App.tsx` matched against a feature directory.
- `app/core/permissions.py` read in full: the catalogue **already defines**
  purchasing, inventory, hr, payroll, expense, reporting and document
  permissions, and the nine system roles already reference them. No permission
  work is needed to start a new module — only enforcement.
- `app/modules/accounting/enums.py` read: `SourceKind` already carries
  `SUPPLIER_INVOICE`, `GOODS_RECEIPT`, `STOCK_MOVEMENT`, `PAYROLL_RUN`,
  `DEPRECIATION`. The posting engine was built expecting these modules.

## Two findings that change the plan

**1. The module registry under-reports what exists.** `registry.py` was last
touched in commit `b9d2bf6`, but `383b660` added the appointment book and
partial refunds without updating it. Three entries are wrong today:

| Registry says | Reality |
| --- | --- |
| clinic missing "appointment scheduling screen" | `AppointmentsPanel.tsx`, 656 lines, driven in a browser |
| cash missing "giving a cash payment back has no screen yet" | `RefundDialog.tsx` + `CashPage.tsx:378`, wired to `POST /cash/sessions/{id}/refunds` |
| dashboard missing "an appointments screen to go with the count" | that screen now exists |

This is the opposite of the failure the registry was built to prevent, but it is
still the registry lying about the system. Correcting it is part of increment 1,
and it is a correction *downward* in no case — nothing is being marked available.

**2. The foundation anticipated phases 4–6 and the groundwork is already laid.**
Permissions, roles, segregation-of-duties pairs, source kinds and the policy
engine all exist for purchasing, inventory, HR and payroll. What is missing from
those modules is models, services, routers and screens — not architecture.

## The matrix

Legend: ● built and verified · ◐ partial · ○ absent

| # | Module | Backend | DB | Frontend | Tests | Status |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | Dashboard | ● | ● | ● | ● | ◐ no trends |
| 2 | Clinic | ● | ● | ● | ● | ◐ recall and reminders |
| 3 | Sales | ◐ | ● | ● | ● | ◐ credit notes, VAT |
| 4 | Cash | ● | ● | ● | ● | ◐ transfers |
| 5 | Banking | ● | ● | ● | ● | ◐ outgoing lines |
| 6 | Purchases | ● | ● | ● | ● | ◐ GRNI accrual (P5) |
| 7 | Inventory | ● | ● | ● | ● | ◐ valuation (I1) |
| 8 | Employees | ● | ● | ● | ● | ◐ statutory timesheet |
| 9 | Payroll | ● | ● | ● | ● | ◐ blocked on P2, 2018 tax (P10), commission (P3) |
| 10 | Accounting | ● | ● | ● | ● | ◐ asset accounting |
| 11 | Reports | ● | n/a | ● | ● | ◐ statutory forms (A2, A3) |
| 12 | Documents | ○ | ○ | ○ | ○ | ○ |
| 13 | Settings | ◐ | ● | ◐ | ◐ | ◐ policy, numbering |

### 1. Dashboard — ◐

**Works:** `GET /clinic/today` derives every figure server-side —
takings split cash/card, debt, advances held, till state, appointment and
treatment counts. Figures are withheld, not zeroed, where the caller may not
read them. 271 lines of frontend logic under unit test.

**Missing:** trend comparison (this week vs last); expenses and bank balance
tiles, which have no source until purchasing and reporting land.

**Blocked on:** nothing. Expense/bank tiles wait on modules 6 and 11.

### 2. Clinic — ◐

**Works:** patients with duplicate review and merge; the appointment book with
overlap refusal; encounters; per-tooth records in FDI notation; item completion,
cancellation and correction *in the service layer*; billing completed items once
each; settlement.

**Missing backend:** `GET /clinic/plans` and `GET /clinic/patients/{id}/plans`
— `POST /clinic/plans` exists alone, so a plan can be created and never read
back. Plan status transitions (`PlanStatus` exists on the model, nothing moves
it). Linking an item to a plan at `record_items` time is possible in the model
(`TreatmentItem.plan_id`, composite FK to `treatment_plan`) and unreachable
over HTTP.

**Missing frontend:** treatment plan screen; correcting a completed item
(`POST /treatments/{item_id}/correct` is API-only).

**Missing DB:** nothing. `treatment_plan` and `treatment_item.plan_id` ship in
migration 0007.

**Blocked on:** nothing.

### 3. Sales — ◐

**Works:** customers, catalogue with versioned prices, invoice draft/issue/void,
payments in three methods with allocation, advances as a liability, partial
refunds of unspent money, receivables ageing, debt write-off.

**Missing:** credit notes — the only way to give back money already applied to
an issued invoice. The service refuses and names what it needs rather than
guessing a posting. VAT-inclusive pricing.

**Blocked on:** A5 (does a dental service bear VAT) for VAT; **A7-shaped gap**
for credit notes — no approved debit account for a credit against revenue.

### 4. Cash — ●/◐

**Works:** registers, sessions with float, receipts, refunds *(screen exists —
registry stale)*, expenses, deposits by staff and by collection, physical count,
approved discrepancy, close posting shortage or overage.

**Missing:** transfers between registers; fiscal receipts; multi-currency tills.

**Blocked on:** nothing for transfers. The clinic has one till, so this is low
value — recorded, not scheduled.

### 5. Banking — ●/◐

**Works:** accounts paired to ledger accounts, CSV/XLSX import with per-account
column mapping, duplicate refusal, 1:1 / 1:n / n:1 matching, card settlement
with commission split, deposit confirmation, ignore-and-reopen.

**Missing:** outgoing lines — a supplier payment or a tax payment arriving on a
statement has nothing to match against until purchasing and payroll exist.

**Blocked on:** B3 (real export format) for import mapping; module 6 for
outgoing matching.

### 6. Purchases — ○

**Everything.** Needs: supplier, purchase request, purchase order, order line,
goods receipt, receipt line, supplier invoice (bill), bill line, supplier
payment, payment allocation. Four distinct states that must not be conflated:
ordered, received, invoiced, paid.

**Has already:** all seven `purchasing.*` permissions; the
`purchasing.order.manage` / `purchasing.order.approve` segregation pair; roles
(accountant registers bills, inventory manager raises orders, director
approves); `SourceKind.SUPPLIER_INVOICE` and `GOODS_RECEIPT`.

**Needs policy rules:** goods received (Dr inventory / Cr goods-received-not-
invoiced), bill registered, supplier payment, supplier advance.

**Blocked on:** nothing for ordering and receiving. Posting on receipt depends
on the inventory valuation decision (I1) only for the *amount*; the accounts
are derivable from the workbook.

### 7. Inventory — ○

**Everything.** Needs: item (material), category, unit of measure, location,
stock movement (the ledger of stock), batch with expiry, stock balance
projection, adjustment with approval, consumption tied to a treatment item.

**Has already:** five `inventory.*` permissions; the inventory-manager role
without self-approval of adjustments; the dentist holds
`inventory.consumption.record` — the model already assumed a dentist records
what they used; `SourceKind.STOCK_MOVEMENT`.

**Blocked on:** **I1 — valuation method is undecided.** This blocks the
*posting* of a consumption or an adjustment, not the stock record itself.
Quantities can be tracked and a low-stock alert raised with no valuation at
all. Plan: ship stock movement as a quantity ledger immediately; gate every
valuation posting behind a policy rule that must be activated, exactly as
revenue tax treatment is gated today.

### 8. Employees — ○

**Everything.** Needs: employee, position, employment record with dates,
attendance day, work schedule. One employee must be able to hold several roles
(the clinic's owner is also its dentist).

**Has already:** three `hr.*` permissions; `hr_payroll_manager` role; the
`app_user` / `membership` tables, which an employee record must *link to*
rather than duplicate.

**Blocked on:** nothing. P1 (Uzbek timesheet code wording) affects labels only
and can ship with the codes shown untranslated and marked.

### 9. Payroll — ●/◐

**Works:** statutory rules as dated, approved, sourced rows with supersession;
salary agreements per employee, a rise being a new row; proration by days
actually worked, read from the timesheet; employee deductions and employer
contributions kept in separate categories; bonuses and agreed deductions, each
requiring a reason; calculate / approve / post / pay as four acts with four
permissions and the calculator barred from approving; payslips that keep their
own amounts, rates and rule versions; one accounting entry per run, built from
the clinic's own posting rules; payslip and run screens in three languages,
driven in a browser.

**Refuses, by design:** a run cannot be approved or posted while a required
statutory rule is unapproved, and the refusal names the rule and the open
question behind it. Because the current social tax rate is unverified (P2),
**every run today is blocked**, which is the control working rather than a gap.
It also refuses a run whose payslips were calculated before the rules now in
force were approved — found by the browser pass, which posted a month of wages
with no tax at all before the guard existed (ADR-063).

**Missing:** 2018's progressive income tax, whose calculation is absent from
the workbook itself (P10); percentage and commission pay, which needs P3;
the statutory payroll reports and certificates.

**Blocked on:** **P2 — the current social tax rate**, which blocks posting
outright; P3 — commission basis; P10 — the 2018 income tax macro. Gross pay,
proration, bonuses and deductions all compute without any of them, and do.

### 10. Accounting — ●/◐

**Works:** chart of accounts (statutory import, marked unconfirmed), fiscal
years and periods, journal draft/post/reverse, period close and audited reopen,
trial balance, account statement, versioned policy with activation and
supersession. Immutability enforced by database trigger as well as service.

**Missing:** balance sheet, P&L, cash flow — these belong to module 11 and are
listed on both.

**Blocked on:** A1 (statutory classification of the seeded chart) keeps the
module at `partial`; A2/A3 for the statements.

### 11. Reports — ◐

**Works:** trial balance, account statement.

**Missing:** P&L, balance sheet, cash flow, receivables ageing *as a report*
(the data exists at `GET /sales/receivables/ageing`), payables ageing, revenue
by period, expenses by category, employee compensation, inventory movements,
reconciliation exceptions.

**Blocked on:** A2 and A3 block *statutory filing forms* only. Every internal
management report above is derivable from the ledger with the
`AccountElement` classification already on each account. The distinction the
brief draws — internal management reports now, statutory forms blocked — is
exactly right and already supported by the data model.

### 12. Documents — ○

**Everything.** Needs: stored document, category, link to a business record,
content storage, access control, download audit.

**Has already:** two `document.*` permissions, held by every role at read level.

**Blocked on:** D1 (retention policy) for *deletion* rules only. Storage
location is an operational decision: local filesystem under the app's data
directory is the only option without new infrastructure, and is what this will
use unless told otherwise.

### 13. Settings — ◐

**Works:** organization profile, users and memberships, roles and permissions
(read-only), duty-separation declaration, system status, audit log viewer,
language and theme.

**Missing:** accounting policy configuration *from settings* (the policy editor
lives under accounting and is reachable, so this is a navigation gap, not a
missing capability), numbering sequences, payment settings, custom role editing.

**Blocked on:** nothing.

## Cross-cutting gaps

- **No component tests.** 129 frontend tests, all of pure functions. Every
  component is covered only by type check, lint, build and a manual browser pass.
- **No browser test in CI.** Manual Chrome passes are recorded in `TESTING.md`.
- **`mypy` advisory** (`continue-on-error`).
- **Registry feature strings are English in all three languages.**
- **The Celery worker has no tasks.**

## Progress against this audit

| # | Increment | State |
| --- | --- | --- |
| 1 | Clinic completion — plans, corrections, registry correction | **Done**, 2026-11-03 |
| 2 | Employees — records, positions, rota, timesheet | **Done**, 2026-11-03 |
| 3 | Purchasing | **Done**, 2026-11-03 |
| 4 | Inventory | **Done**, 2026-11-04 |
| 5 | Payroll | |
| 6 | Reports | **Done**, 2026-11-04 (taken before payroll, at the owner's request) |
| 7 | Documents | |
| 8 | Dashboard and navigation | |
| 9 | Hardening | |

Four defects were found that no test had caught:

- a documented security control that did not exist (ADR-049);
- a module registry under-reporting three finished features;
- `hr` reaching into `identity.models`, caught by the architecture guard and
  fixed by giving identity the directory methods the other module needed
  (ADR-050);
- a missing accounting policy rule reported to the user as **"the requested
  record was not found"**, found by driving purchasing in a browser. It is
  now its own error — `policy_rule_missing`, a 409 naming the operation whose
  rule is absent (ADR-051). This also improves the existing debt write-off
  case, which has been failing with that same unhelpful 404 since Phase 3.

**Inventory was started, backed out, and then built.** The first attempt was
reverted mid-increment rather than committed with models that had no
migration — every test run builds the database from `base`, so the suite
would have failed on a tree that looked complete. It was rebuilt on
2026-11-04 to the design below, which survived the pause intact:

- **Stock is a ledger, not a column.** No `quantity_on_hand`; what is on a
  shelf is the sum of its movements. Stock is counted physically often enough
  that a stored total's drift gets found, usually by somebody blaming the
  person who counted.
- **Movements carry quantities, never amounts.** What materials cost is
  already in the books — account 1010, debited when the supplier invoice was
  registered. An amount on a movement would be a second place deciding it.
- **Valuation is declared, not assumed.** `inventory_valuation_method` on the
  organization, `UNDECLARED` by default, exactly the shape of the VAT
  treatment beside it: until an accountant answers `I1`, stock is counted and
  never costed, and a consumption records a quantity and posts nothing.
  `WEIGHTED_AVERAGE` is derivable from the supplier invoices that billed each
  delivery, with no new storage; `FIFO` would need cost layers and would say
  it is not implemented rather than guess.
- **The treatment-to-material link belongs to clinic, not inventory.** Clinic
  sits above inventory in the layering, so it may refer down to a stock
  movement; inventory referring up to a treatment is the dependency that
  stops a modular monolith being modular.

A fifth defect was found while building inventory, and only a browser could
have found it: the HTTP schema for a goods receipt line had no `item_id`, so
a delivery recorded through the API named no material and **reached no
shelf**, while every service test passed because the service itself was
right. The gap was in the translation between the two. The regression test
now sits at the HTTP layer, where the defect lived.

One small finding is recorded and **not** fixed, to avoid re-verifying a
screen mid-increment: the patient list's open control is labelled "Edit"
(`PatientsPanel.tsx:104`, using `t.common.edit`) although it opens a
read-mostly account page. Two e2e scripts click it by that name. Worth
correcting together with them.

## Order of work

Dependency-driven, which mostly agrees with the brief's suggested order:

1. **Clinic completion** — plans and corrections. No dependencies; closes the
   oldest open module. Registry correction rides along.
2. **Employees** — before payroll, and before documents (employee documents) and
   before purchasing can name who ordered something. Smallest new module; it
   establishes the pattern for the four that follow.
3. **Purchasing** — suppliers, orders, receipts, bills, payments, payables.
   Unblocks banking's outgoing lines and the dashboard's expense tile.
4. **Inventory** — depends on goods receipt from (3) for stock to arrive
   through the correct door. Quantity ledger first, valuation postings gated.
5. **Payroll** — depends on (2). Gross and net ship; statutory lines gated.
6. **Reports** — depends on (3), (4), (5) for payables, inventory and
   compensation reports to have data. Internal reports only.
7. **Documents** — independent, deliberately late: it attaches to records that
   must exist first.
8. **Dashboard and navigation integration** — the expense and bank-balance
   tiles, and a registry that matches reality.
9. **Hardening** — component tests, a browser suite in CI, concurrency and
   idempotency tests across the new modules.

Cash transfers and credit notes are recorded above and not scheduled: one is
low value for a single-till clinic, the other is blocked on an unapproved
posting rule.
