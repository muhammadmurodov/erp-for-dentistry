# Implementation status

**Last updated:** 2026-11-04 · **Phases 1–2 complete; Phase 3 sales, receivables, cash and banking complete and verified in a browser; Phase 3c clinic complete; employees, purchasing, inventory and management reporting built on top of them. A full module audit is in `MODULE_AUDIT.md`; payroll, documents and hardening are the remaining work.**

The authoritative record of what works. The application serves the same
information from `GET /api/v1/platform/modules`, and an integration test
asserts nothing beyond Phase 1 reports itself as `available`.

## Verified results

Every figure below was produced by running the command shown, not estimated.

```
backend:  925 passed, 2 skipped        .venv/bin/python -m pytest tests
lint:     All checks passed!       .venv/bin/ruff check app tests alembic
schema:   No new upgrade operations detected.   alembic check
migrate:  downgrade base -> upgrade head: clean (every test run)
frontend: 242 passed (242)         npm test
          built in 1.14s           npm run build
          0 problems               npm run lint
          0 errors                 npm run typecheck
api e2e:  18 passed, 0 failed      HTTP checks against the running API
          Phase 3d: 6 HTTP slice tests, in the suite above
          Refunds, appointments and the till: 19 more HTTP slice
          tests, in the suite above
ui e2e:   Phase 1: 12 screens driven in Chrome, each inspected
          Phase 2: 11 accounting steps driven in Chrome, 0 console errors
          Phase 3: 10 sales steps driven in Chrome, 0 console errors
          Phase 3b: 10 cash steps driven in Chrome, 0 console errors
          Phase 3d: 18 banking steps driven in Chrome, 0 console errors
          Phase 3c: 31 clinic steps driven in Chrome, 0 console errors
          Policy:   10 policy steps driven in Chrome, 0 console errors
          Small clinic: 28 steps driven in Chrome, 0 console errors
          Refunds:  14 refund steps driven in Chrome, 0 console errors
          Plans:    12 plan and correction steps, 0 problems
                    (frontend/e2e/plans.cjs)
          Employees: 14 hiring, rota and timesheet steps, 0 problems
                    (frontend/e2e/employees.cjs)
          Purchasing: 15 order-to-payment steps, 0 problems
                    (frontend/e2e/purchasing.cjs)
          Inventory: 11 steps — the shelf, the refusal, the count and
                    the separation — 0 problems, run twice
                    (frontend/e2e/inventory.cjs)
          Reports:  11 steps — every statement against the ledger,
                    the drill-down, the export and the blocked forms
                    — 0 problems, run twice
                    (frontend/e2e/reports.cjs)
          Re-run after each increment: clinic, refunds, appointments,
                    plans, employees — all 0 problems
          Appointments: 15 steps driven in Chrome, 0 console errors
                    — all kept as scripts under `frontend/e2e/`,
                      re-runnable, 0 problems reported on a repeat run
sql e2e:  12 database invariants verified by raw SQL with the service bypassed
          Phase 3d: 9 more, same method
docker:   both images build, run, and serve
```

## Phase 1 — Foundation · Complete

Measured against the project's ten-point definition of done. Legend: ✅ met ·
— not applicable.

| Feature | Rules documented | Migration | Endpoints | Frontend | Authorization | Validation | Accounting | Audit | Tests | Demonstrable |
| --- | :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: |
| Project structure, Docker Compose | ✅ | — | — | — | — | — | — | — | ✅ | ✅ |
| PostgreSQL schema and migrations | ✅ | ✅ | — | — | — | ✅ | — | — | ✅ | ✅ |
| Organizations and multi-tenancy | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | — | ✅ | ✅ | ✅ |
| Authentication, token rotation | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | — | ✅ | ✅ | ✅ |
| Roles and granular permissions | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | — | ✅ | ✅ | ✅ |
| Segregation-of-duties controls | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | — | ✅ | ✅ | ✅ |
| User and membership administration | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | — | ✅ | ✅ | ✅ |
| Append-only audit trail | ✅ | ✅ | ✅ | ✅ | ✅ | — | — | ✅ | ✅ | ✅ |
| Application shell, uz/ru/en | ✅ | — | — | ✅ | ✅ | — | — | — | ✅ | ✅ |
| Health checks, structured logging | ✅ | — | ✅ | — | — | — | — | — | ✅ | ✅ |
| Monetary primitives (`Decimal`) | ✅ | — | — | ✅ | — | ✅ | ✅ | — | ✅ | ✅ |
| Statutory reference data | ✅ | — | — | — | — | ✅ | — | — | ✅ | ✅ |
| CI pipeline | ✅ | — | — | — | — | — | — | — | — | ✅ |

### What a user can actually do

Sign in; be forced to change an initial password; see their organization,
roles and resolved permissions; read and edit the organization profile; list
members with their roles and last sign-in; add a member with roles; change a
member's roles (and be refused, with the rule named, when the combination
breaks a control); revoke a member's access; browse the audit trail with
filters and keyset pagination, expanding before/after values; switch between
organizations; change language and theme; and see every unfinished module
marked as such with a list of what is and is not built.

### Endpoints

18 routes. All verified live.

```
GET    /health/live                        GET    /api/v1/platform/modules
GET    /health/ready                       GET    /api/v1/platform/permissions
POST   /api/v1/auth/login                  GET    /api/v1/organization
POST   /api/v1/auth/refresh                PATCH  /api/v1/organization
POST   /api/v1/auth/logout                 GET    /api/v1/organization/currencies
GET    /api/v1/auth/me                     GET    /api/v1/users
POST   /api/v1/auth/change-password        POST   /api/v1/users
POST   /api/v1/auth/switch-organization    PUT    /api/v1/users/{id}/roles
GET    /api/v1/audit/logs                  POST   /api/v1/users/{id}/deactivate
GET    /api/v1/roles                       PUT    /api/v1/organization/duty-separation
```

### Database-level guarantees, verified

| Guarantee | Verified by |
| --- | --- |
| `audit_log` rejects UPDATE and DELETE | `test_audit_integrity.py` + manual `psql` |
| Cross-tenant role grant is unrepresentable | Raw-SQL insert refused by composite FK |
| An audited organization cannot be deleted | `ON DELETE RESTRICT`, tested |
| System roles cannot belong to a tenant | `CHECK` constraint |
| Role codes unique globally and per tenant | Two partial unique indexes |
| Migrations reverse cleanly | Exercised on every test run |

## Phase 2 — Accounting engine · Complete (module reports **partial**)

The engine is built, enforced at the database level, tested and driven in a
browser. The module reports `partial` rather than `available` because the
statutory classification of the seeded chart is still unconfirmed
(`OPEN_QUESTIONS.md` A1) — the only remaining reason, now that the policy has
an interface.

| Feature | Rules | Migration | Endpoints | Frontend | Authz | Validation | Accounting | Audit | Tests | Demo |
| --- | :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: |
| Chart of accounts + statutory import | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Fiscal years and monthly periods | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Journal entries: draft, post | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Reversal mechanism | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Period close and audited reopen | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Idempotent posting (`PostingEvent`) | ✅ | ✅ | — | — | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Trial balance | ✅ | — | ✅ | ✅ | ✅ | ✅ | ✅ | — | ✅ | ✅ |
| Account statement + general ledger | ✅ | — | ✅ | ✅ | ✅ | ✅ | ✅ | — | ✅ | ✅ |
| Accounting policy versioning | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Discarding a draft version | ✅ | — | ✅ | ✅ | ✅ | ✅ | — | ✅ | ✅ | ✅ |

### Database-level accounting guarantees, verified

Each was checked by bypassing the service and attempting the violation in raw
SQL. All twelve produced the intended refusal with a readable message.

| Guarantee | Enforced by |
| --- | --- |
| A posted entry balances | `trg_journal_entry_balanced` + `trg_journal_line_balanced` (deferred to `COMMIT`) |
| A posted entry cannot be edited | `trg_journal_entry_immutable` |
| A posted entry cannot be deleted | same trigger |
| A posted line cannot be edited or deleted | `trg_journal_line_immutable` |
| Recording a reversal link *is* allowed | same trigger, by exception |
| A closed period rejects postings | `trg_journal_entry_period_open` |
| An entry dated outside its period is refused | same trigger |
| A structural account cannot be posted to | trigger-filled `account_is_postable` + `CHECK` |
| A cross-tenant line is unrepresentable | composite FK `fk_journal_line_entry` |
| A line cannot be both debit and credit | `ck_journal_line_one_side_only` |
| A negative amount is refused | `ck_journal_line_amounts_not_negative` |
| The same business event cannot post twice | `uq_posting_event_source` |

### What a user can do

Import the 368-account national chart (marked provisional); open a fiscal year
and get twelve monthly periods; write a manual journal entry with live
balance checking; save it as a draft or post it; post a draft separately;
reverse a posted entry with a reason; read a trial balance that states
whether it balances; drill into any account's statement with a running
balance; close a period and reopen it with an audited reason.

And, as the owner — the only role that may: build a starting policy from the
legacy workbook and see where each rule came from; read the rules of the
version in force; start a new version, add or change its rules, and put it
into force, which supersedes the previous version without restating anything
posted under it; throw away a draft that was a mistake. Every other role with
`accounting.policy.read` sees the same screen with no control that changes it.

### Accounting endpoints

25 routes under `/api/v1/accounting`: accounts (list, get, create,
deactivate, seed the statutory chart), fiscal years (list, create), periods
(list, close, reopen), journal (list, get, create, post, reverse, delete a
draft), policies (list, list rules, create, build from the workbook, set a
rule, activate, discard a draft), reports (trial balance, account statement).

## Phase 3 — Sales and receivables · Complete (module reports **partial**)

Billing works end to end and posts through the accounting engine. The module
reports `partial` because a revenue posting's **tax split** is still
unanswered (`OPEN_QUESTIONS.md` A5): an invoice can only be issued under the
"no tax split" treatment, which is what every fiscal receipt on file shows.

| Feature | Rules | Migration | Endpoints | Frontend | Authz | Validation | Accounting | Audit | Tests | Demo |
| --- | :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: |
| Customers (billing identity) | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | — | ✅ | ✅ | ✅ |
| Service catalogue with versioned prices | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | — | ✅ | ✅ | ✅ |
| Invoices: draft, issue, void | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Payments: cash, card, transfer | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Allocation, partial and multi-invoice | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Advances held as a liability | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Refunds by reversal | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Receivables ageing | ✅ | — | ✅ | ✅ | ✅ | ✅ | ✅ | — | ✅ | ✅ |
| Default policy from the workbook | ✅ | — | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |

### Database-level receivable guarantees, verified

| Guarantee | Enforced by |
| --- | --- |
| A payment cannot be over-allocated | `trg_payment_allocation_within_limits` (deferred) |
| An invoice cannot be over-allocated | same trigger |
| The same payment cannot be applied twice to one invoice | `uq_payment_allocation_payment_invoice` |
| An issued invoice's amounts are immutable | `trg_invoice_amounts_immutable` |
| An issued invoice's lines are immutable | `trg_invoice_line_immutable` |
| A posted payment's amount is immutable | `trg_payment_amount_immutable` |

### The worked demonstration

Driven in a browser and checked against the ledger: a 300 000 invoice, a
120 000 part payment, then a 500 000 payment auto-applied oldest-first. The
trial balance ends at cash 620 000 = advance liability 320 000 + revenue
300 000, with the receivable back to zero and **revenue recognised exactly
once**. The overpayment is a liability in account 6310 — the statutory
"advances received from customers" — not revenue.

## Phase 3b — Cash sessions · Complete (module reports **partial**)

| Feature | Rules | Migration | Endpoints | Frontend | Authz | Validation | Accounting | Audit | Tests | Demo |
| --- | :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: |
| Cash registers | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | — | ✅ | ✅ | ✅ |
| Sessions with an opening float | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Cash payments into the drawer | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Cash expenses | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Bank deposits, staff or collection route | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Physical count and discrepancy | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Close posting shortage or overage | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |

### Database-level cash guarantees, verified

| Guarantee | Enforced by |
| --- | --- |
| One open session per register | `uq_cash_session_one_open_per_register`, a partial unique index |
| A closed session cannot be changed | `trg_cash_session_immutable` |
| A non-open session takes no movements | `trg_cash_movement_follows_session` |
| A payment cannot be counted into a drawer twice, or paid back out of one twice | `uq_cash_movement_payment_kind` (ADR-040) |
| A deposit must name its route | `ck_cash_movement_deposit_needs_a_route` |

**Cash is also refunded here, and nowhere else.**
`POST /cash/sessions/{id}/refunds` reverses the payment and records the money
leaving this drawer in one transaction; the generic sales refund route refuses
a cash payment and says so (ADR-040). The session's summary already reports
refunds alongside receipts, expenses and deposits, and since the refund
dialog was built it is also where one is issued — in full, or part of one
(ADR-045).

### The worked example

The specification's scenario, driven in a browser: open the till, take
300 000 in cash, pay a 15 000 courier out of it, deposit 200 000 to the bank,
count 5 000 short with a reason, close. Expected cash 135 000 against a count
of 130 000; the shortage posts to *other operating expenses*, and the
ledger's cash account ends holding **exactly what was counted**.

## Phase 3d — Banking and reconciliation · Complete (module reports **partial**)

The module reports `partial` because the bank's real export format is still
unconfirmed (`OPEN_QUESTIONS.md` B3) and a bank charge on anything but a card
settlement has no agreed treatment (B4). Import works regardless: the
importer discovers the file's shape rather than assuming it (ADR-035).

| Feature | Rules | Migration | Endpoints | Frontend | Authz | Validation | Accounting | Audit | Tests | Demo |
| --- | :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: |
| Bank accounts paired to a ledger account | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | — | ✅ | ✅ | ✅ |
| Per-account column mapping | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | — | ✅ | ✅ | ✅ |
| Statement import, CSV and Excel | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | — | ✅ | ✅ | ✅ |
| Duplicate lines refused on re-import | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | — | ✅ | ✅ | ✅ |
| One-to-one, one-to-many, many-to-one matching | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Card settlement with the commission split out | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Cash deposits confirmed against transit | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Lines set aside with a reason, and reopened | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | — | ✅ | ✅ | ✅ |
| Import runs kept with their counts and problems | ✅ | ✅ | ✅ | ✅ | ✅ | — | — | ✅ | ✅ | ✅ |

### What a user can actually do

Pair a bank account with the ledger account its balance must equal; see which
column headings the importer already recognises, and teach it this bank's own;
upload a statement as the bank exported it and see how many lines were read,
imported, already held and not understood — with the line number of each row
it would not guess at; work a queue of unexplained lines filtered by account
and status; see what a line could account for, with each record's unmatched
remainder; match one line to several records or several records to one line,
declaring what the acquirer withheld; watch the arithmetic before sending it;
set a line aside with a reason and bring it back.

### Database-level banking guarantees, verified

Each was checked by bypassing the service and attempting the violation in raw
SQL.

| Guarantee | Enforced by |
| --- | --- |
| What a bank reported cannot be edited | `trg_bank_transaction_evidence_immutable` |
| A match cannot be re-pointed or re-valued | `trg_reconciliation_match_immutable` |
| A line cannot be over-matched | `trg_reconciliation_match_coherent` (deferred) |
| A line cannot claim to be reconciled when it is not | same trigger, both directions |
| A fully accounted line cannot stay in the queue | same trigger |
| A line set aside takes no matches | same trigger, checked first |
| The same record cannot be matched to one line twice | `uq_reconciliation_match_pair` |
| The same statement line cannot be imported twice | `uq_bank_transaction_fingerprint` |
| A statement amount cannot be negative | `ck_bank_transaction_amount_positive` |
| Setting a line aside without a reason is refused | `ck_bank_transaction_ignoring_needs_a_reason` |
| A cross-tenant statement line is unrepresentable | composite FK `fk_bank_transaction_account` |

### The worked example

The specification's card settlement, driven over HTTP end to end
(`tests/integration/test_phase3d_slice.py`): a patient pays 500 000 by card,
which debits the card transit account in full; the acquirer withholds 5 000
and 495 000 lands on the statement; importing that statement posts
**nothing**; matching it clears the transit account to exactly zero, leaves
the bank account holding exactly the 495 000 the bank reported, and records
the 5 000 as an expense rather than a silent shortfall. Revenue is unchanged
by the whole exercise — **500 000, not 995 000**, which is what the legacy
workbook's treatment would have produced.

A second line on the same statement — the bank's own maintenance fee — is set
aside with a reason and then brought back, because a line nobody can explain
yet must not be silently dropped.

### Banking endpoints

13 routes under `/api/v1/banking`: accounts (list, create, set column
mapping, deactivate), the recognised column headings, statements (import,
list runs), transactions (list, get with its matches, candidates, match,
ignore, reopen).

### Clinic endpoints

27 routes under `/api/v1/clinic`: the day's summary for the dashboard,
patients (search, register, detail, edit,
merge), duplicate reviews (list, resolve), appointments (list, book,
reschedule, change status) and the dentists one may be booked with,
encounters (open, complete, list for a patient), treatment items
(record, list, complete, cancel, correct), treatment plans (create), billing
(billable items, draft an invoice), and settlement (the remaining balance,
take a payment, payment history).

**117 routes in the API overall** — 115 under `/api/v1`, plus the two
health checks. Counted from the generated OpenAPI document, one per
path-and-method.

## Phase 3c · Clinic — the money side

What works, and what deliberately does not.

**Patients.** Registration with duplicate detection on name plus birth year
and on telephone number, never refused on a suspicion: the candidates come
back with the new record and a review is queued. Merging keeps the losing
record and requires a reason. The clinic has never had a patient number, so
the stable code the system issues is new, and the review queue exists because
name and birth year is the only key the clinic has ever used.

**The clinical record** exists as models, services, routes and audit events —
encounters, per-tooth treatment items in FDI notation, plans spanning visits,
completion, cancellation and correction. **It has no screens.** A dentist
cannot yet record a tooth from a browser.

**Settlement.** One submission takes cash, card and an applied advance against
one bill, inside the invoice's row lock, after a single check of the whole
submission against the remaining balance. The remaining balance is derived
everywhere and is an input nowhere: the request schema has no field for it.
Overpayment is refused by the service, by a database trigger that re-sums
under the same locks, and by the panel, in that order of authority. A
resubmitted form replays its first outcome instead of taking the money twice.

**Two ledger defects were found and fixed** before any of this was built, by
driving the real services and reading the trial balance afterwards: removing
an allocation left the ledger reporting a receivable the subledger said was
outstanding, and refunding a payment whose advance had been applied left
account 6310 — a liability — carrying a debit balance. Both are ADR-038, both
have regression tests, and the trial balance balanced throughout, which is why
nothing had caught them.

**The screens have now been driven in a browser** as the two people who
share the work — reception registers and may not take money, the cashier takes
money and may not register — and the pass is kept as `frontend/e2e/clinic.cjs`
so it can be re-run rather than re-done. It found three defects, all in what
the panel showed rather than what it did, listed below. It also checks the
ledger at the end: issuing the bill recognises the revenue, and taking the
money clears the receivable and moves the cash without recognising it again.

**A third defect was found while testing this work**: the over-allocation
trigger from migration 0003 counted reversed allocations, so after ADR-038 a
legitimate re-application would have been refused at COMMIT. No test could
have caught it — the trigger was deferred and the tests never commit. Migration
0008 rewrites it (ADR-039).

**A fourth was found the same way, in the cash drawer.** `cash_movement` was
unique on `(organization_id, payment_id)` — the right rule stated too broadly,
because it also made a refund row for a payment that already had a receipt
impossible to write. So refunding a cash payment reversed the ledger and left
the session still expecting the money: the day's count came up short by exactly
the refund, and the close posted real cash to 9430 as a variance that did not
exist. It is C-3 in the other direction. Migration 0009 narrows the constraint
to `(organization_id, payment_id, kind)`, cash is refunded from an open session
through `POST /cash/sessions/{id}/refunds`, and the generic sales refund route
now refuses a cash payment and says where to go (ADR-040).

## Phase 3c · The clinical screens · Built

A dentist can now record a visit from a browser. Everything behind this was
already built and tested; what was missing was somewhere to type.

**What works.** Open a visit against a patient with a complaint; record work
one row per tooth, in FDI notation, with surfaces and a finding; mark an item
finished; cancel one with a mandatory reason; finish the visit with clinical
notes; and draft a bill from the finished work. Tooth numbers are validated
at the keystroke against the same ranges the backend enforces — permanent
11–18, 21–28, 31–38, 41–48 and deciduous 51–55, 61–65, 71–75, 81–85 — and an
empty tooth box is accepted, because a consultation belongs to no tooth.

**A visit does not imply a bill.** Recording work does not price it. Only a
`COMPLETED` item is billable, and billing drafts an invoice rather than
issuing one: issuing recognises the revenue and keeps its own permission, so
the dentist who did the work does not decide the clinic has earned it.

**The same work cannot be billed twice.** An item carries the invoice line it
produced and leaves the billable list once billed; the list is never cached,
because a stale one would offer work another screen has already invoiced.

### The access policy, provisionally

While `OPEN_QUESTIONS.md` C1 is unanswered, clinical content sits behind
`clinic.clinical.read`: the dentist and the owner see findings, teeth and
notes; reception and the cashier see a patient, their account and their
bills, and no clinical panel at all. This is the strict reading, so any later
decision loosens it, which is the safe direction.

**One change was needed to make that work**, and it is a correction rather
than a relaxation. `GET /clinic/patients/{id}` required *both*
`clinic.patient.read` and `sales.invoice.read`, so it refused the dentist
outright — who may read a patient and record treatment but not read invoices
— which put the entire clinical workflow out of reach of a browser. The
money half of that response is now withheld (`balance: null`, no invoice
rows) rather than the whole page refused, exactly as the dashboard withholds
what it may not show (ADR-044). Nobody gained a permission.

The cashier, who may raise a bill but may not read a finding, sees **how
many** finished treatments are waiting and bills all of them; the itemised
list with findings stays behind the clinical permission.

### Verified end to end in a browser

`frontend/e2e/patient-to-payment.cjs` drives the whole journey as the three
people who do it — 23 steps, 0 problems: reception registers and finds a
patient and is refused the clinical record; the dentist opens a visit,
records tooth 36 MO with a finding, has tooth 99 refused, marks it finished,
finishes the visit, and is refused billing; the cashier bills it; the same
work cannot be billed again; issuing moves 600 000 to revenue and the
receivable; an overpayment is blocked by the form; a mixed 200 000 cash and
400 000 card settles it; a settled bill refuses more money; and the ledger
ends with revenue recognised once, the receivable at zero and the cash
exactly what went into the drawer. Driven in all three languages and at
phone width.

### Not built

Treatment plans have no screen. Correcting a completed item is API-only.
These are declared in the module registry and visible on Settings → System
status. Appointments used to head this list; the book is built, below.

## The dashboard · Rebuilt around the clinic's day

The home screen used to show the organization's registration details and a
list of module statuses. Both were accurate and neither was any use at eight
in the morning. It now answers the questions a three-person clinic actually
opens it for: what was taken today, what is still owed, what advances are
held, whether the till is open, who is coming in, and what work is unfinished
or unbilled.

**Every figure is derived on the server and borrowed, never restated**
(ADR-044). `GET /clinic/today` composes them from the services that own each
definition — `ReceivablesService.ageing` for the debt, `BillingService` for
the takings and advances, `CashSessionService.open_drawer` for the till — so
the dashboard cannot disagree with the ageing report or the cash screen. A
test asserts the debt matches the report to the som.

**A figure the reader may not see is absent, not zero.** Each section is
gated on the permission that guards the screen it summarises, and a withheld
section is not queried at all. The clinic-wide debt follows
`reporting.financial.read` — the permission that guards the ageing report —
rather than the weaker `sales.invoice.read`, because an aggregate is a
capability of its own and a summary screen is not a way around it.

**No decorative controls.** A quick action appears only where the person may
act *and* the screen exists, which is why there is no "record a treatment"
button yet.

The technical module list moved to **Settings → System status**, generated
from the backend registry with no status label hardcoded in the frontend.

Verified: 19 backend cases for the read model, 27 frontend cases for the
display rules, and the screen driven in Chrome as the owner, the receptionist,
the cashier and the accountant — four different sets of tiles, in dark and
light, in all three languages, at desktop and phone widths.

## Fitted to a clinic of three · Complete

The clinic this is built for is run by two or three members of one family.
Two things were adjusted for that, and nothing else was: the system was not
redesigned for a different business.

**An organization may declare that it cannot separate duties** — the owner's
setting, with a mandatory reason, audited both ways (ADR-041). Before it, a
cashier who counted the till and found it short **could not close the session
at all**, because closing with a discrepancy needs an approval permission the
cashier role does not hold; and a cashier could not give back a payment they
had just taken in error. Neither refusal produces a second person. It
produces an open drawer overnight and, eventually, three people sharing the
owner's login — which does not weaken the audit trail so much as empty it.

The declaration grants nobody a permission. It removes only the requirement
that an approver be a **different** person, and every act relying on it
carries `self_approved` in its audit record. It does not relax the read-only
rule, and it is read from the organization inside the request, never from a
request body.

Worth stating because it bounds the change: every combination the clinic
actually wants — receptionist with cashier with dentist — was allowed all
along. `director` is the only role whose combination with another trips a
control, and while the declaration stands the assignment screen no longer
offers it.

**No approval workflow was added, on purpose.** Routing a merge or a refund
to a second authority means routing it to nobody here.

**The duplicate-patient review queue is built** (ADR-042). Registration is
never refused on a suspicion; the queue shows both records side by side with
every identifying field, says which record a merge would keep before anybody
presses anything, and offers exactly two outcomes — *different people* or
*same person* — each needing a note, with the merge behind a second explicit
confirmation naming what is kept and what is filed away. The superseded
record is kept and marked, never deleted.

### How far this is verified

To the project's own ten-point standard, last column included. Migrations
`0010` and `0011` round-trip and `alembic check` is clean; 61 backend cases
and 18 frontend cases cover the rules, the audit records, the permission
delivery and both dead ends in both states — including seven over HTTP,
because the refund gate lives in the router and only a request exercises it.

**Both screens have been driven in a real Chrome**, as the owner, the cashier
and then reception, and the pass is kept as `frontend/e2e/small-clinic.cjs`
so it can be re-run rather than re-done. It checks that the declaration is
refused to everybody but the owner, that it grants no permission to anybody,
that a cashier may then close their own short till with the close recorded as
`self_approved` while the shortage still reaches account 9430, and that no two
patients merge without a person confirming it twice. It leaves the tenant as
it found it, declaration included.

**It found two defects, and neither could have been found any other way** —
numbers 45 and 46 below. One of them had been shipped a commit earlier.

## Giving part of a payment back · Complete

Refunding a payment in full was the only refund the system could record, and
it is not the refund this clinic gives. The commonest one by a distance is
the patient who hands over 800 000 for a bill of 600 000 and wants the
200 000 back. There was no way to record it, which means it either did not
happen at the desk or it happened off the books.

**Each refund is now a record of its own** (ADR-045), and what is left
refundable is derived — the payment, less what is applied to bills, less what
has already been handed back — so a second refund can follow the first and
neither needs a stored remainder to disagree with. Only the unapplied part
may be given back: money applied to an issued invoice is revenue against work
the clinic has billed for, and handing it back without crediting the invoice
would refund the patient and leave them owing for the same treatment. That
case needs a credit note, which the clinic has no approved rule for, so the
service refuses and says which instrument is missing.

**The ledger entry is the advance posting, reversed** — the same policy
rule's two accounts the other way round. Nothing new is mapped, no new rule
is needed, and it therefore works against a policy version that predates the
feature.

**The drawer and the books move together.** A cash refund leaves the open
till by the same movement ADR-040 built, and migration `0012` turns that
movement's uniqueness rule into a partial index so several partial refunds of
one payment are allowed while one refund can still only be paid out once. The
day's takings are reported net of the day's refunds, because a tile reading
"taken today, cash" that ignored money handed back would not agree with the
drawer — and the drawer is the one figure a cashier can check by counting.

**There is now a screen**, which there was not: `RefundDialog`, reached from
the receipt on the cash page. It reads `GET /payments/{id}/refundability`
before it offers anything, so the figure the cashier sees is the figure the
service will enforce, and it refuses to submit more than there is. The
arithmetic is in `lib/refund.ts` on `bigint` minor units, with 17 tests.

**Verified in a real Chrome** as the cashier, kept as `frontend/e2e/refunds.cjs`:
take 800 000 for a 600 000 bill, read what the dialog says is refundable, try
to take more than there is, hand back part of it twice, then check the three
things no page can tell you on its own — that the till's expected cash fell
by exactly what went out, that the ledger agrees, and that the bill the
patient really did pay is still settled. It opens and closes its own drawer.

## The appointment book · Built

Reception can book the day from a browser: who is coming in, who has arrived,
who is still expected. It was the last part of the clinic that the dashboard
counted but no screen could change.

**The day and the hour are the clinic's own** (ADR-046). `date.today()` is
the container's day, which is UTC; the clinic is five hours ahead, and
between seven in the evening and midnight those are different dates — which
is exactly when a dental clinic is still seeing patients. Business dates now
come from `app.core.clock`; instants stay UTC. An appointment's time of day
is a local `Time`, not a timestamp, because the clinic books "Tuesday at
three" and that is not an instant.

**One dentist cannot be in two chairs** (ADR-047). Two receptionists booking
the same hour at the same moment is serialised by an advisory lock on the
dentist and the day — neither transaction can see the other's uncommitted
row, and there is no row to lock — so the second is refused with a 409 that
names the booking it clashed with. The form warns before it sends; the server
refuses regardless. A booking with no hour claims no chair.

**No appointment status posts anything.** Confirmed, arrived, finished,
cancelled, no-show: none is an accounting event, and a test asserts a day's
worth of them leaves the ledger untouched. The clinic's own register shows
several attendances a week that produce no revenue.

**Verified in a real Chrome** as reception and then the cashier, kept as
`frontend/e2e/appointments.cjs`: book into a free hour, provoke the clash and
read both the warning and the refusal, confirm and admit a patient, move a
booking to the next day, cancel one with a reason, check the dashboard counts
what was booked, and check that the cashier reads the day and is offered no
control that changes it. It finds its own free hour rather than assuming one
and gives the hours back at the end, so it can be run twice in a row.

## Phases 4–7 · Not started

| Phase | Scope | Blocked on |
| --- | --- | --- |
| 6 (part) — Statutory reports | Balance sheet, P&L, cash flow | A2, A3 |
| 4 — Purchasing and inventory | Suppliers, orders, receiving, supplier invoices, stock movements, payables | I1 (valuation method) |
| 5 — Employees and payroll | Employees, contracts, compensation, attendance, payroll runs, approval, posting | **P2 (current tax rules)**, P3 (commission basis) |
| 6 — Financial reporting | Balance sheet, P&L, cash flow, AR/AP ageing, dashboards | A2 (statutory forms), A3 (cash flow method) |
| 7 — Hardening | Integration and concurrency testing, security review, performance, backup and verified restore, deployment | D1 (retention policy) |

## Known gaps

Stated plainly rather than left to be discovered.

**Still no component tests.** Six frontend modules are now tested —
`money`, `fdi`, `dashboard`, `patientIdentity`, `refund` and `appointments`,
129 cases between them — but every one of them tests a pure function. The
rule a component applies is covered; the component applying it is covered
only by the type check, the lint, the build and a browser pass.

**A treatment plan has no screen.** The encounter, the per-tooth record and
the appointment book are built. Planning future work, and correcting a
completed item, are still API-only.

**Crediting an issued invoice has no instrument.** Partial refunds give back
only money the patient has not spent (ADR-045). Giving back money already
applied to an issued bill needs a credit note, which has no approved rule —
the service refuses and names what is missing, rather than guessing a
posting.

**No automated browser test in CI.** The UI has been driven in a real Chrome
and inspected screen by screen (see `TESTING.md`), but that was a manual run.
A Playwright suite in CI is Phase 7 work.

**`mypy` is advisory in CI.** `continue-on-error` until the codebase is fully
annotated.

**Module feature lists are English in every language.** The "what is not
available yet" banner on sales, cash and banking shows the backend registry's
`missing_features` verbatim, and those strings are English prose that cites
internal documents (`OPEN_QUESTIONS B3`). It is the most prominent
untranslated text in the application. Fixing it means deciding whether the
registry should carry three languages per feature, or whether that prose
belongs in front of a user at all (ADR-015 put it there deliberately, to keep
unfinished modules honest). Pre-dates Phase 3d; the banking pass is where it
became obvious.

**A statement is imported in the request, not on the worker.** A few thousand
rows parse in well under a second, so the upload is handled synchronously and
the 10 MB cap keeps it that way. A bank that exports a year at a time would
need the worker.

**The Celery worker has no tasks.** It is declared in Compose so the topology
is real, and it prints that no tasks are defined rather than pretending to
process a queue. Report generation is its Phase 6 work.

**No backup or restore.** Phase 7. A backup that has never been restored is
not a backup.

**Organization creation is CLI-only.** Creating a tenant is a platform-level
act; there is no HTTP endpoint for it. Deliberate for now.

**The default policy is still unreviewed.** The workbook-derived rules are
shipped as a draft with each rule's source note attached, and the screen says
so in two places, but nobody with accounting authority has confirmed them
(`OPEN_QUESTIONS.md` A1, A4, A5).

**Role editing is read-only in the UI.** Roles and their permissions can be
inspected but not edited. The shipped roles encode the segregation-of-duties
controls, and a custom-role editor needs those checks surfaced properly rather
than bolted on. Listed as missing on the settings module.

**Rate limiting falls open if Redis is unreachable.** Deliberate — a
rate-limiter outage should not take the API down — and it logs a warning. With
a single process and no Redis, limits are per-process and the log says so.

## Things found and fixed during Phase 1

Recorded because each is a class of bug worth not reintroducing.

1. **A rejected sign-in's lockout counter was being rolled back**, making
   account lockout unreachable in production. The test fixture had been more
   forgiving than production and hid it. Fixed by committing security side
   effects before re-raising, and by making the fixture mirror production.
2. **The `director` role violated a segregation-of-duties control** it was
   meant to enforce — it held both `purchasing.order.manage` and
   `purchasing.order.approve`. The role was corrected, not the rule.
3. **A module dependency cycle** between `organization` and `identity`,
   caught by the boundary test. Resolved by extracting a `provisioning`
   module above both (ADR-019).
4. **Implicit lazy loads** failing as `MissingGreenlet` far from the cause.
   Resolved by declaring relationships `lazy="raise_on_sql"` (ADR-010).
5. **A cached global engine leaking across event loops**, failing only when
   the whole suite ran. Resolved with `NullPool` under test.
6. **Twelve statutory timesheet codes have no Uzbek text** in the source. Not
   invented; the gap is declared in the data file and a test fails if it
   changes without being re-declared.
7. **The source chart of accounts has an incomplete hierarchy** and one
   miscased parent reference. Classification is derived from code ranges
   instead, cross-checked against observed postings, and flagged unconfirmed.

### Found while building Phase 2

15. **A reversal could not record what it reverses.** `reversal_of_entry_id`
    is inside the immutability trigger's protected set — it must be, or a
    posted reversal could later be re-pointed at a different original — so
    setting it *after* the row was posted was refused. It is now set at
    creation.
16. **A superseded policy stopped answering for its own dates**, which defeats
    the point of effective dating (ADR-023).
17. **A PL/pgSQL `CASE` over record fields does not compile** when the
    branches reference different row types; the balance trigger needed
    explicit `IF` branching.
18. **The validation error handler returned 500 instead of 422.** Pydantic
    leaves the original `ValueError` object in an error's context, which
    `JSONResponse` cannot serialise — so any custom validator crashed the
    request it was meant to reject politely.
19. **Two mechanisms were cascading one column** (ADR-024).
20. **`entry.__dict__` omitted unassigned columns**, so a freshly created
    entry failed response validation on its NULL reversal link. Responses are
    now built from attributes.
21. **Report dataclasses are slotted and have no `__dict__`** — the two
    report endpoints would have failed at runtime. They use
    `dataclasses.asdict` now.

### Found by running the app in a browser

None of these were visible to the type checker, the linter or the tests.

8. **A stale route survived a change of user.** Signing out and back in as a
   different person left the app on the previous URL — a cashier landed on
   `/settings/audit`, a page their role cannot open. The route now resets on
   sign-in, sign-out, session expiry and organization switch, and an
   unpermitted settings tab redirects instead of showing a refusal.
9. **CORS rejected the dev frontend.** `localhost:5173` and `127.0.0.1:5173`
   are different origins to a browser; only the first was allowed. Both
   loopback spellings are now listed for development, with a note that
   production should list only the real origin.
10. **The dashboard listed modules the user could not open**, contradicting
    the navigation beside it. Both now filter on the same permission.
11. **Every `<select>` stretched to full width**, wrapping the sign-in header.
    The shared control class forced `w-full`, which beat callers' `w-auto`
    because Tailwind resolves conflicts by CSS source order.
12. **The current user was labelled "(sign in)"** in the user list — a reused
    translation key that meant nothing there. Now "(you)", in all three
    languages.
13. **The role dialog always displayed the conflict sentence**, reading as a
    verdict on the current selection even when nothing conflicted. It is now
    neutral guidance; the real conflict still comes back from the backend
    naming the specific rule.
14. **The sidebar status legend had no dots**, so it explained nothing, and a
    missing favicon 404'd on every page load.

## Phase 2 UI additions found and fixed

22. **The journal dialog was too narrow for its line grid**, so content
    escaped the dialog frame. Dialogs now take a size and their body scrolls.
23. **A `<select>` in a grid cell sized itself to its longest option** — a
    long Uzbek account name — widening the whole layout past its container.
    Fixed with `minmax(0, 1fr)` tracks and a `min-w-0` default on `Select`.

## Found while building Phase 3d

24. **A statement's delimiter was being guessed wrong, silently.**
    `csv.Sniffer` inspects the opening characters, and a bank statement opens
    with a title block rather than a table — so it gave up, the fallback
    chose a comma, and a semicolon-separated statement parsed as one very
    wide column with no recognisable headings. The error the operator saw
    blamed their column mapping. Each candidate separator is now scored by
    how well it explains the file (ADR-035).
25. **The coherence trigger reported the wrong violation first.** Matching
    against a line that had been set aside produced "fully accounted for but
    not marked reconciled" — a bookkeeping problem the operator does not
    have — because the IGNORED check sat below the accounting checks. The
    order of checks in a trigger is part of its interface.
26. **The settlement posting cleared the transit account by the gross
    amount**, which left the withheld fee stranded there and the bank account
    overstated. Each matched record now bears its share of the fee, so what
    posts is the net — which is what the statement line shows.
27. **A duplicate match was refused by the unique index mid-flush.** The
    refusal was correct but arrived as an `IntegrityError`, which aborts the
    transaction, so a many-to-one match could not continue past it. The pair
    is checked before inserting; the index stays as the backstop for a race.
28. **Over-matching was reported in preference to the real problem.** Adding
    a record a line already held came back as "these records come to more than
    the statement line" because the arithmetic ran before the duplicate
    check. The specific refusal is the useful one, so it runs first.
29. **A totals footer was reported as a broken row on every import.** The
    statement's own "Итого" line has amounts and no date, which is exactly
    what a mis-mapped row looks like. Footer labels are now recognised in
    three languages and skipped quietly; anything else without a date is
    still reported with its line number, because a silently dropped row is
    how a third of a statement goes missing.
30. **The banking module read `sales.models` and `cash.models` directly**,
    which the architecture test refuses. Resolved by adding the two read
    methods to the services that own those records (ADR-036) — the rule about
    which payments are still awaiting a third party's money now lives in one
    place.
31. **The API client could not send a file at all.** It set
    `Content-Type: application/json` for any body and `JSON.stringify`d it,
    so an upload would have been a quoted empty object. `FormData` now passes
    through untouched, with the boundary left for the browser to write.

## Found by running Phase 3d in a browser

Driven as the accountant who would really use the screens. None of these were
visible to the type checker, the linter or 394 backend tests.

32. **A statement could be imported into the wrong bank account, silently.**
    The upload dialog preselected the first account alphabetically, so
    creating an account and immediately importing put the file somewhere
    else — and the only symptom is a high "already held" count, because the
    lines look like duplicates of that other account's. It now preselects
    only what the page filter already names, or the single account if there
    is only one, and otherwise insists on a choice. The same `useState`
    also froze its value before the accounts had loaded.
33. **The reconciliation grid used `1fr` instead of `minmax(0, 1fr)`** — the
    exact defect recorded as number 23 for Phase 2, reintroduced. A grid
    track sized `1fr` will not shrink below its content, so the statement
    table pushed the page 91px sideways in Uzbek at 1280px.
34. **A card's scrolling table never scrolled.** `Table` wraps itself in
    `overflow-x-auto`, but a card is usually a grid item, and a grid item's
    default `min-width: auto` is wide enough to hold its content — so the
    card stretched instead. `Card` is now `min-w-0`, which fixes it wherever
    a card holds a wide table.
35. **A card header with three controls was wider than a phone.** The action
    group was `flex shrink-0`, which holds it at full width and pushes the
    page sideways rather than moving one control to the next line. Both
    `CardHeader` and `PageHeader` now wrap their actions. Measured across
    every screen, three languages and five widths: nothing overflows now,
    which was not true of the app before this change either.
36. **The match dialog's two figures contradicted each other.** "Still
    unexplained: 495 000 · Matched: 495 000" reads as a sum that does not
    work; the first is the line's target and the second is what the current
    selection accounts for. They are now labelled as what they are.
37. **The candidate list pushed the controls off the screen.** Five
    unmatched payments — one ordinary day — put the commission field, the
    note and the running total below the fold. The list has its own scroll
    area now, so the two things being decided stay in view.
38. **The queue's status column was cut off** whenever the detail panel was
    open: six columns in a 628px pane. The document number was dropped from
    the queue, since the detail panel beside it already shows it. Measured:
    the table now fits without scrolling in all three languages.

## Found by running the policy editor in a browser

The accounting policy decides which accounts every business event posts to,
so it is the screen with the furthest reach in the application — and until
now it had none. Building it and driving it found two defects in code that
had been passing its tests since Phase 2.

39. **Activating a version that starts before the one in force threw a 500.**
    Superseding closes the prior version "the day before" the new one starts;
    if the new one starts first, that date lands before the prior version
    began and `ck_accounting_policy_effective_range_ordered` refuses the
    UPDATE. The browser showed it as a CORS failure, because a 500 never
    reaches the middleware that adds the headers — which is the useful tell:
    a CORS error on one endpoint and not the others means the server fell
    over. It is now refused with both dates named, and has two tests.
40. **A draft version was a dead end.** Its effective date cannot be edited,
    so a draft that starts before the version in force can never be
    activated — and while a draft exists no new version can be created. The
    only escape was `psql`. A draft can now be discarded, with its rules,
    which is safe for exactly the reason a draft journal entry can be
    deleted: it has posted nothing, so the ledger does not refer to it. The
    discard is audited before the row goes.
41. **The detail panel queried a version it had just deleted**, producing a
    404 in the console after every discard. The selection now only survives
    while the version it names still exists.

## Found by running the clinic in a browser

Driven as reception and then as the cashier. The arithmetic underneath was
right in all three — every one of these is about what the screen said.

42. **The remaining balance read "0" while the form held more than was
    owed.** `computeRemaining` clamps at zero, correctly: a balance owed
    cannot be negative, and `money.ts` has a test saying so. But the panel
    rendered that clamp beside the words "That is more than is owed", so at
    the exact moment a cashier had typed too much the figure they look at
    said the bill was covered. Over the balance, the field now shows the
    amount the form is over by — `−1 000 UZS` — and the clamp stays where it
    belongs.
43. **A payment form was laid out in full for somebody who may not submit
    one.** A receptionist opening a bill got the cash, card, advance and
    reference fields, enabled, with "You do not have access to this section"
    *underneath* them. The refusal is now stated above the form and the
    fields are disabled, so nobody fills in an amount before finding out.
44. **The cash drawer was explained twice on one screen**, once as an alert
    and again as a hint under the cash field, one line apart. Said once now,
    in the alert.

## Found by running the small-clinic verification in a browser

Two defects, both invisible to the test suite, and the reason this project
drives a browser against a real database rather than trusting a green run.

45. **A permission added to the catalogue never reached a database already in
    service.** `permission` and the system roles' grants are seeded once, by
    migration `0001`, which reads the application's own catalogue at migration
    time. On a fresh database that is always current — and every test run
    rebuilds from `base`, so the catalogue and the database always agreed
    there. On a database migrated before a permission was written, nothing
    delivered it. Two permissions were dead in every existing deployment:
    `organization.duties.declare`, which made the duty-separation declaration
    unreachable (the owner was refused their own setting with a 403, which is
    how this was found), and `sales.debt.write_off`, which had shipped a
    commit earlier with its route, its service and its audit event, held by
    nobody. Migration `0011` delivers both, additively and idempotently, from
    a frozen list that imports nothing (ADR-043); a declared catalogue in
    `tests/security/test_permission_delivery.py` fails the suite the next time
    somebody adds a permission without a migration behind it.
46. **A patient search matched names against telephone numbers.** Any query
    containing a digit was reduced to its digits and matched as a substring of
    `phone_normalized`, so searching the surname `Karimova031930` returned
    patients called `Oripov031930` — their number ends in those digits — and
    searching a patient code like `P-00015` returned strangers whose number
    contains `00015`. Showing one patient's record to somebody looking for
    another is not a cosmetic fault. A query is now treated as a telephone
    number only when it contains no letters and at least four digits, which
    keeps `90 123 45 67` finding `+998901234567` and stops a two-digit query
    matching most of the register.

## Found by running the refund and appointment passes in a browser

Three defects. The first two are the same mistake in two modules, and both
turned a refusal the code was written to explain into a 500 with a request
id — the kind of fault that is invisible to a service-level test, because
inside one transaction the attribute being read is still loaded.

47. **Opening the till twice answered a 500, not a 409.** The conflict
    handler built its message from `register.code` *after* the failed flush.
    A failed flush leaves the session needing a rollback, so touching an
    expired attribute raised `PendingRollbackError` instead. The message
    existed, was correct, and never arrived. The value is now read before the
    flush, and `tests/integration/test_duplicate_open_session_slice.py` keeps
    it honest — deliberately an integration test, because the service-level
    test cannot see this.
48. **The same mistake in allocation.** `payment.number` and
    `invoice.number` were read inside the `IntegrityError` handler for a
    double allocation, so applying the same payment to the same invoice
    twice produced a 500 where a 409 was written.
49. **Reception was offered an empty dentist picker.** The appointment form
    listed dentists from `GET /users`, which needs `identity.user.read`.
    Reception does not hold it — and reception is the only role that books
    anything, so the feature was unusable by precisely the people it was
    built for, while working perfectly for the owner every test signed in
    as. Widening reception's permissions would have been the wrong fix: a
    receptionist has no business reading staff e-mail addresses and sign-in
    history. `GET /clinic/dentists` answers a name and an id behind
    `clinic.appointment.read` instead (ADR-047).

The appointments pass also found two faults in itself, worth recording
because they are the ones that make a verification script quietly useless: it
booked a fixed hour, so its second run collided with its first and reported
the clash rule working as a fault; and it probed for existing staff by trying
to sign in as them, which spent the login rate limit on itself and then read
the refusal as "that account does not exist". Both are fixed in the script —
it finds a free hour, gives the hours back when it finishes, and reads the
directory instead of guessing.

## Courses of treatment, and corrections · Complete

The last two clinical workflows that had no screen. Both were already built
and tested behind the API; what was missing was somewhere to use them.

**A course of treatment** holds work that spans visits — the clinic's register
shows prosthetic work running across days as a matter of course, an impression
one day and a fitting later. `treatment_plan` has existed since migration
0007 and `POST /clinic/plans` since Phase 3c, with no way to read a plan back
afterwards. Added:

- `GET /clinic/plans` (filtered by patient and state) and
  `GET /clinic/plans/{id}`, which returns the plan with the work on it and a
  count of what is still open.
- `POST /clinic/plans/{id}/status`, with the transitions held as data rather
  than a chain of `if`s, so a refusal can name what *would* have been allowed.
  A plan moves draft → proposed → agreed → in progress → finished, or is
  abandoned from any live state. A proposal may go back to draft, because a
  patient asking for a change before agreeing is the ordinary case and
  re-drafting is honest where editing a proposal in place is not.
- **A plan cannot be finished while a tooth on it is still open.** It is
  refused rather than silently cancelling that work: what happens to an
  unfinished tooth is the dentist's decision, not the plan screen's.
- The screen: a panel on the patient's record, a plan detail with its
  progress, and a selector when recording work so a tooth can be attached to
  a course as it is written down. Only open courses are offered — attaching
  new work to a finished plan would reopen a decision somebody already made.

**Correcting a treatment** now has a dialog. It is not an edit: the original
stays on the record marked `CORRECTED`, pointing at its replacement, with the
reason kept beside it. The button is offered for a completed item too — that
is the case it exists for, the tooth written down wrong and noticed
afterwards — and stops at the bill, because once an item has reached an
invoice the money has moved and the invoice is corrected first.

### A security control that was documented but absent

`SECURITY.md` has stated since Phase 1 that `clinic.clinical.read` is marked
sensitive "so such reads are audited". The permission was enforced, the event
`clinic.clinical.read` was defined, and it was listed in
`REQUIRED_AUDIT_ACTIONS` — but **nothing ever wrote it**. Reading a patient's
treatment record left no trace of who had looked, which is exactly the
question a privacy complaint asks.

Reading an encounter list, a treatment list or a plan now writes that audit
row and commits it, which a `GET` otherwise would not. The document now
describes a control the system has.

### How far this is verified

- 25 new backend tests (`tests/clinic/test_plans.py`): the life cycle, every
  illegal transition, the completion gate, cancelled work not holding a plan
  open, both audit records, and tenant isolation on read and on write.
- 14 new frontend unit tests (`src/lib/plans.test.ts`) over the transition
  table the screen uses to decide what to offer.
- `frontend/e2e/plans.cjs`: 12 steps in a real Chrome, 0 problems — including
  that a draft never offers "Finished", that a plan with work outstanding
  explains itself and disables the button, that a correction is refused
  without a reason and keeps the original, that the **cashier is shown no
  finding, no treatment record and no course of treatment** while keeping the
  patient account they do need, and that the page fits a phone in Uzbek.

### The registry was stale, and is now correct

Commit `383b660` shipped the appointment book and partial refunds without
updating `registry.py`. The application was under-reporting itself in three
places: clinic claimed to be missing an appointment screen that existed, cash
claimed refunds had no screen, and the dashboard listed an appointments screen
as a gap. All three corrected, and the two new clinical workflows added. The
registry is the one thing in this system allowed to decide whether a module
says it works, so it lying in the *modest* direction is still it lying.

Clinic remains `partial`, for a narrower reason than before: a treatment
cannot yet record the materials it consumed, because inventory does not exist.

## Employees · Built

Phase 5's first half, and the module the four after it depend on: payroll has
nobody to pay without it, an employee document has nothing to attach to, and
purchasing cannot record who asked for something.

**An employee is not a user** (ADR-050). `app_user` is how somebody signs in;
`employee` is somebody the clinic employs. The link is nullable — a cleaner
can be paid without ever signing in — and unique per organization, so one
person cannot acquire two records and therefore two payslips. Roles stay on
the membership and are *shown* here, never stored: one person holding several
roles already worked, and a copy would be a second answer to what they may do.

The module **never creates a login or grants a role**. Hiring somebody and
giving them authority over the books are two decisions with two permissions,
and an endpoint doing both would be a privilege-escalation path through a
module nobody would think to audit.

The module **stores no money**. No salary, no rate, no accrual.

### What a user can actually do

- Keep a staff list, searched by name or reference, filtered by state.
- Define positions — a job title, which is not the same as a role: the
  clinic's owner holds the `owner` role and is employed as a dentist.
- Link somebody to their existing login; the picker offers only members who
  have no employee record yet.
- Set an ordinary week as a rota, and see it back.
- Fill in a monthly timesheet as a grid, one row per person, one cell per
  day — worked, absent, sick, leave or holiday. Recording a day again
  replaces it and keeps the old value in the audit trail.
- Record that somebody left, with a date and a reason. The record is kept.

### Database-level guarantees, verified

| Guarantee | Mechanism |
| --- | --- |
| An employment is `ENDED` exactly when it has a leaving date | `ck_employee_ended_has_date` |
| Nobody leaves before they started | `ck_employee_ended_after_hired` |
| Hours exist only on a day that was worked | `ck_attendance_day_hours_match_code` |
| One attendance row per person per day | `uq_attendance_day_employee_date` |
| One login belongs to one employee | `uq_employee_organization_id_user_id` |
| An attendance row cannot name another tenant's employee | composite FK `(organization_id, employee_id)` |

### Endpoints

`/hr/positions` · `/hr/employees` · `/hr/employees/linkable-users` ·
`/hr/employees/{id}` · `/hr/employees/{id}/status` · `/hr/employees/{id}/end` ·
`/hr/employees/{id}/schedule` · `/hr/attendance` · `/hr/attendance/summary`

### How far this is verified

- 85 backend tests across `tests/hr/`: the employment life cycle, every
  illegal transition, the login-link rules, attendance refusals, the month
  grid's year boundary, and 15 API tests covering who may reach each endpoint.
- 19 frontend unit tests over the transition table and the calendar
  arithmetic the timesheet uses.
- `frontend/e2e/employees.cjs`: 14 steps in a real Chrome, 0 problems —
  including that the screen offers no way to create a login, that a sick day
  offers no hours field, that a cell before somebody started is not
  clickable, that an ended employment stops offering moves, and that the
  **cashier is offered the module nowhere and told why if they ask for it
  directly**.

### A boundary the architecture guard caught

`hr` imported `identity.models` directly — a layering violation
`tests/test_module_boundaries.py` has guarded against since Phase 1, and the
first time it has actually fired. Fixed by giving `IdentityService` the three
things the other module genuinely needed — `list_members`, `is_member` and
`role_codes_for` — returning a `MemberSummary` of three fields rather than the
`User` row, so a module above identity cannot reach `password_hash` and start
making decisions that belong there.

### Why it still reports `partial`

The clinic's legal timesheet uses twelve single-letter statutory codes whose
Uzbek wording is unconfirmed (`OPEN_QUESTIONS.md` P1). The module's five codes
are its own and the screen says so in the legend. Mapping onto a form somebody
signs needs the real wording and is not guessed.

## Purchasing · Built

Phase 4's first half, and the largest single increment so far: nine tables,
twenty endpoints, seventy backend tests.

**Four records, not four flags** (ADR-052 in spirit, stated in migration
0014). Ordering, receiving, invoicing and paying are separate tables with
their own numbers and dates, because the questions a clinic actually asks
span them: what did we order that has not arrived, what has arrived that we
have not been billed for, what are we billed for that we have not paid. A
single `purchase` row with booleans answers none of them, and is how a
supplier who invoices twice gets paid twice.

### What posts, and what deliberately does not

| Event | Posts |
| --- | --- |
| Purchase request | nothing — somebody is asking |
| Purchase order, approved | **nothing** — a promise to buy is not a transaction |
| Goods receipt | **nothing** — see below |
| Supplier invoice, drafted | nothing |
| Supplier invoice, registered | Dr materials 1010 or expense 9420 / Cr payable 6010 |
| Supplier payment, against a bill | Dr 6010 / Cr cash 5010 or bank 5110.01 |
| Supplier payment, with no bill | Dr advances paid 4310 / Cr cash or bank |
| Advance applied to a bill | Dr 6010 / Cr 4310 |

Five of those seven rules come **straight off the clinic's workbook sheet**.
The exception is `supplier_invoice_expense`, which the sheet has no row for —
it records only the cash side of rent and utilities — and its source note
says so and asks for confirmation.

**A goods receipt posts nothing, and that is a decision.** The textbook
accrual debits stock and credits a goods-received-not-invoiced account on the
day goods arrive. The workbook has no such account; inventing one would be
inventing a statutory classification, which this project does not do. The
receipt records what arrived and the invoice does the posting. The gap is
real — it only shows when a delivery crosses a period end — and is recorded
as `OPEN_QUESTIONS` **P5** rather than papered over.

### The guarantee the shape exists for

A delivery line may be invoiced **once**. `_check_receipt_line` refuses a
second bill against it, refuses one from a different supplier, and refuses
the same line twice on one invoice. Beside it,
`uq_supplier_invoice_supplier_reference` means one supplier's own invoice
number may appear once. Between them, the commonest way a small business
loses money to a supplier is unrepresentable.

### Paying from the till

The clinic pays most suppliers in cash, out of the drawer it takes patient
money into. One act, two records that must not come apart: the posting that
reduces the payable and the movement that reduces the cash. It lives in a
composite service **in the cash module** — `cash/services/supplier_settlement.py`
— because cash sits above purchasing in the layering and composes it, the
same shape as `clinic/services/settlement.py`. The drawer is checked *before*
the payment is created, so a closed or empty till refuses the whole act
rather than leaving a posted payment with no movement behind it.

### Database-level guarantees, verified

| Guarantee | Mechanism |
| --- | --- |
| One supplier invoice number per supplier | `uq_supplier_invoice_supplier_reference` |
| A bill's status cannot contradict its own dates | `ck_supplier_invoice_registered_has_time`, `ck_supplier_invoice_void_has_reason` |
| Goods turned away say why | `ck_goods_receipt_line_rejection_has_reason` |
| One drawer movement per supplier payment | `uq_cash_movement_supplier_payment` (partial) |
| A line cannot belong to another tenant's order | composite FK on every child table |

### How far this is verified

- 70 backend tests across `tests/purchasing/`: that an order and a delivery
  post nothing, that a part delivery leaves the right quantity outstanding,
  that over-delivery leaves nothing outstanding rather than a negative, that
  a delivery line cannot be billed twice, the segregation-of-duties refusals,
  partial payment, advances, allocation reversal, ageing buckets, and paying
  from the till including the refusal when the drawer is short.
- 16 frontend unit tests, including money arithmetic done in integer minor
  units — `sumAmounts(['9007199254740993.00', '1.00'])` is exact, which a
  JavaScript number would not be.
- `frontend/e2e/purchasing.cjs`: 15 steps in a real Chrome, 0 problems,
  reading the **trial balance between each step** to prove that nothing
  posted before the invoice was registered and that registering moved
  account 6010 by exactly the invoice total.

### A defect this increment found in Phase 3 code

Registering an invoice under a policy that has no rule for it reported **"The
requested record was not found"**. That is a 404 from `resolve_policy_rule`,
and it has been the response to the deliberately-unmapped debt write-off
since Phase 3 as well: the system's most carefully designed "fail loudly" was
failing quietly and misleadingly. It is now `PolicyRuleMissing` — a 409 whose
message names the operation whose rule is absent (ADR-051). This matters at
deployment, because a clinic upgrading into a new module meets it on that
module's first use.

### Why it still reports `partial`

Goods received before their invoice are not accrued (`OPEN_QUESTIONS` P5);
what the materials are worth and where they sit afterwards is inventory's
question (`I1`); and a supplier payment on a bank statement has nothing to
match against until banking's outgoing lines are built.


## Inventory · Built

Phase 4's second half, and the increment that finally lets a treatment say
what it used. Seven tables, 18 endpoints, 73 backend tests.

**Stock is a ledger, not a column** (ADR-052). There is no `quantity_on_hand`
anywhere in this module: what is on a shelf is the sum of the movements that
put it there and took it away, computed when asked, exactly as an account
balance is the sum of its journal lines. A stored total is a second answer
that drifts — and a clinic counts its shelves often enough that the drift
gets found, usually by somebody blaming the person who counted. The test that
pins this deletes a movement and watches the balance change.

**Counted long before it is costed** (ADR-053). `OPEN_QUESTIONS.md` I1 — the
valuation method — has never been answered, and nothing here guesses it. The
organization declares `inventory_valuation_method`, `UNDECLARED` for
everybody, the same shape as the revenue tax treatment beside it. While it is
undeclared, quantities are tracked, low stock is flagged, expiry is warned
about, and `GET /inventory/valuation` returns **every quantity with every
cost null** rather than refusing the page. Declaring weighted average derives
the cost from the supplier bills already on file, with no new storage; FIFO
says it is not implemented rather than averaging and calling it FIFO.

**Stock arrives through one door** (ADR-054). There is no endpoint that
creates a receipt. Recording a delivery in purchasing is what puts stock on a
shelf — the receipt line names the material and the accepted quantity becomes
a movement in the same transaction — so every quantity traces back to a
delivery note. The API test asserts the *absence* of `POST
/inventory/receipts`, because an absence nobody checks gets filled in later
by somebody being helpful. Only what was accepted is shelved: goods turned
away at the door never entered the clinic.

**A shelf cannot go short.** A consumption, a write-off or a transfer that
would take more off a shelf than is on it is refused under an advisory lock
on the item and the location — the same shape as the appointment book's lock,
because the conflict is with a row the other transaction has not committed.
The refusal names what is actually there, since the next thing the person
does is go and look.

**Counting and approving are two people.** A count is proposed by whoever
counted and approved by somebody else; nothing moves until approval, and the
approver may not be the counter even when one person holds both permissions,
which in a clinic of three they will. The difference is recomputed *at
approval*, not taken from the snapshot, because stock legitimately moves
between counting and approving.

### What a user can actually do

- Keep a list of materials with their units, categories and shelves.
- See what is on each shelf, by lot, and totalled per material.
- Be warned about what is running out — only for items somebody set a level
  on, because a made-up threshold trains people to ignore the list.
- Be warned about lots going out of date, and only while stock is left on
  them: a used-up lot is not a problem however old it is.
- Record a material used on a tooth, from the treatment itself.
- Move stock between shelves as two movements that balance to zero.
- Write off breakage or expiry, with a reason, always audited.
- Count a shelf, propose the difference, and have somebody else approve it.

### Database-level guarantees, verified

| Guarantee | Mechanism |
| --- | --- |
| A movement of nothing is impossible | `ck_stock_movement_quantity_not_zero` |
| A receipt cannot remove stock, a consumption cannot add it | `ck_stock_movement_sign_matches_kind` |
| An adjustment or write-off always says why | `ck_stock_movement_reason_required` |
| One lot number per material | `uq_stock_batch_item_code` |
| A material counted once per count sheet | `uq_stock_adjustment_line_once` |
| A decided count knows who decided it and when | `ck_stock_adjustment_decided_has_time` |
| One default shelf per organization | `uq_stock_location_single_default` |
| No child row can name another tenant's parent | composite FK on every table |

### Endpoints

`/inventory/items` · `/inventory/categories` · `/inventory/locations` ·
`/inventory/batches` · `/inventory/stock` · `/inventory/stock/low` ·
`/inventory/stock/expiring` · `/inventory/movements` ·
`/inventory/consumption` · `/inventory/write-offs` · `/inventory/transfers` ·
`/inventory/counts` (+ propose, approve, reject) · `/inventory/valuation` ·
and `/clinic/treatments/{id}/materials`, which is how a dentist records what
a tooth consumed.

### How far this is verified

- 73 backend tests across `tests/inventory/`: the ledger, the negative-stock
  refusals by every route, batches and expiry, low stock, the count's life
  cycle and its separation of duties, tenant isolation, and that a delivery
  and a consumption both leave the trial balance untouched.
- 29 frontend unit tests over the quantity arithmetic and the rules the
  screens apply.
- `frontend/e2e/inventory.cjs`: 11 steps in a real Chrome, 0 problems, run
  twice in a row — including that the stock screen offers no way to create
  stock by hand, that taking 11 of 10 is refused and says what is there, that
  the count sheet shows the difference as it is typed, that the owner who
  counted is told why they cannot approve it and is offered no button, that
  the server refuses the self-approval too, that the cashier is refused the
  module outright, and that the page fits a phone in Uzbek.

### A defect this increment found, which only a browser could

The service put stock on a shelf correctly from the first commit, and every
service test passed. But `ReceiptLineInputModel` — the HTTP schema — had no
`item_id`, so a delivery recorded **through the API** named no material and
reached no shelf. Nothing below the API layer could have caught it: the
service was right, the tests called the service, and the gap was in the
translation between them. It is now fixed in the schema and the router, with
a regression test at the HTTP layer where the defect actually lived.

The second was in the screen rather than the system: the shelf read
**"10.0000 box"** where a clinic counts ten boxes. `NUMERIC(20, 4)` is the
right column and the wrong thing to show somebody (ADR-056).

### Why it still reports `partial`

What the stock is worth is undeclared (`OPEN_QUESTIONS` I1), so quantities
are kept and value is refused; posting the cost of a material as it is used
waits on the same answer; and FIFO is deliberately not implemented.


## Management reporting · Built

What an owner opens at the end of a month. Five reports, 11 endpoints, 61
backend tests, and the correction of a placeholder that had been standing in
front of two working reports since Phase 2.

**A statement is the trial balance rearranged** (ADR-057). Profit and loss
and the balance sheet are built from `ReportService.trial_balance()` — the
same call the trial balance screen uses — grouped by account element and
summed. Neither issues a ledger query of its own, so neither can disagree
with the ledger, and the balance sheet's result for the year equals the
profit and loss's net result because they are the same rows. Both properties
are asserted rather than assumed.

**Nothing is classified silently** (ADR-058). The seeded chart is
unconfirmed while `OPEN_QUESTIONS.md` A1 is open, and an account may carry no
element at all. Such an account is reported in its own `unclassified`
section and counted, never folded into revenue to make the statement tidy —
dropping it would balance the statement by losing money, and guessing where
it belongs would invent a statutory classification. The balance sheet
returns `balances` and `difference`, and when it does not balance the screen
says so in red rather than rounding it away.

**Cash is what the clinic configured** (ADR-059). The cash statement's
accounts are the ledger accounts its own tills and bank accounts are
attached to, not a guessed account range. Movements are attributed to the
other side of their own entry and grouped by that account; they are
deliberately **not** split into operating, investing and financing, because
which method a filing requires is A3 and unanswered — and the screen says
so. A transfer between two cash accounts is excluded, since it changes no
total.

**Statutory forms stay blocked, and say why** (ADR-061). `GET
/reporting/catalogue` is the backend's own statement of what this build can
produce; the screen renders it rather than a hand-written list. The three
blocked reports name the question blocking each: A2 for the statutory
statement forms, A3 for the statutory cash flow, A5 for a VAT return.

### What a user can actually do

- Run profit and loss over any window, with named periods (this month, last
  month, this quarter, year to date, last year) or chosen dates, and compare
  it with the same window a year earlier.
- Run a balance sheet as at any date, with the same day a year earlier
  beside it.
- See what patients owe and what the clinic owes suppliers, aged, from the
  same services those modules' own screens use — not a second implementation.
- See what came in and went out, and what each movement was for.
- **Click any line and read the entries behind it**, over the window the
  report covers, behind the same permission as the report itself.
- Download any report as a spreadsheet, or print it — the print stylesheet
  drops the shell and forces black on white, so "Save as PDF" is a clean
  page (ADR-060).

### How far this is verified

- 61 backend tests across `tests/reporting/`: empty windows, both ends of a
  window, a day either side, drafts excluded, a voided invoice netting to
  nothing, partial payments, advances as a liability rather than revenue, a
  closed period reporting identically to before it closed, tenant isolation,
  reconciliation against the trial balance, the cash statement reconciling,
  every route swept for the permission, the catalogue's blocked entries, and
  that the downloaded workbook is a real workbook carrying the figures.
- 26 frontend unit tests over period arithmetic — quarters, year boundaries,
  29 February stepping back to the 28th — exact variance in minor units, and
  reading the capability catalogue.
- `frontend/e2e/reports.cjs`: 11 steps in a real Chrome, 0 problems, run
  twice — it bills a patient a known amount and then checks every statement
  moved by exactly that much, with the trial balance read alongside as a
  second opinion. It fails if the page ever shows a "not developed" message
  again.

### Why it still reports `partial`

The statutory filing forms are not built and are not guessed: their formats
and line mappings are `OPEN_QUESTIONS.md` A2, the cash flow method is A3,
and a VAT return depends on A5. Every *management* report a clinic runs is
built and reconciles to the ledger.

## Next step

The order comes from `docs/MODULE_AUDIT.md`, which audited all thirteen
modules against the code rather than against this document:

1. ~~**Employees**~~ — done, above.
2. ~~**Purchasing**~~ — done, above.
3. ~~**Inventory**~~ — done, above.
4. **Payroll** — depends on (1). Gross cost and net pay ship; every statutory
   line is produced only from an approved, versioned rule, and a run holding
   an unapproved one refuses to post and names it. `P2` and `P3` stay
   unanswered and nothing is guessed.
5. ~~**Reports**~~ — done, above. Statutory filing forms stay blocked on
   `A2` and `A3`.
6. **Documents** — deliberately late: it attaches to records that must exist.
7. **Dashboard and navigation integration**, then **hardening**: component
   tests, the browser passes promoted into CI, concurrency and idempotency
   across the new modules.

**Blocked until answered:** `OPEN_QUESTIONS.md` A5 (VAT — still decides
whether a revenue posting splits into net plus a tax liability), A7 (which
account takes a written-off debt — the operation is built and permissioned but
has no policy rule, so it fails loudly), B4 (a bank charge on a deposit or
transfer), C1 (clinical access rules), C4 (whether the paper register's
figures are money at all), S2 (the price list), S3 (whether the clinic bills
before it collects), I1 (inventory valuation), P2 and P3 (payroll tax and
commission), A2 and A3 (statutory report forms and cash flow method).

A5 remains the one to ask first: it shapes every revenue posting, and three
modules now depend on the answer rather than one.
