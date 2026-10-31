# Implementation status

**Last updated:** 2026-10-31 · **Phases 1–2 complete; Phase 3 sales, receivables, cash and banking complete, and verified in a browser.**

The authoritative record of what works. The application serves the same
information from `GET /api/v1/platform/modules`, and an integration test
asserts nothing beyond Phase 1 reports itself as `available`.

## Verified results

Every figure below was produced by running the command shown, not estimated.

```
backend:  399 passed in 85.75s     .venv/bin/python -m pytest tests
lint:     All checks passed!       .venv/bin/ruff check app tests alembic
schema:   No new upgrade operations detected.   alembic check
migrate:  downgrade base -> upgrade head: clean (every test run)
frontend: built in 1.45s           npm run build
          0 problems               npm run lint
          0 errors                 npm run typecheck
api e2e:  18 passed, 0 failed      HTTP checks against the running API
          Phase 3d: 6 HTTP slice tests, in the suite above
ui e2e:   Phase 1: 12 screens driven in Chrome, each inspected
          Phase 2: 11 accounting steps driven in Chrome, 0 console errors
          Phase 3: 10 sales steps driven in Chrome, 0 console errors
          Phase 3b: 10 cash steps driven in Chrome, 0 console errors
          Phase 3d: 18 banking steps driven in Chrome, 0 console errors
          Policy:   10 policy steps driven in Chrome, 0 console errors
                    — both kept as scripts under `frontend/e2e/`, re-runnable
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

17 routes. All verified live.

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
GET    /api/v1/roles
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

18 routes under `/api/v1/accounting`: accounts (list, get, create,
deactivate, seed the statutory chart), fiscal years (list, create), periods
(list, close, reopen), journal (list, get, create, post, reverse, delete a
draft), policies (list, list rules, create, build from the workbook, set a
rule, activate, discard a draft), reports (trial balance, account statement).
**86 routes in the API overall.**

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
| A payment cannot be counted into a drawer twice | `uq_cash_movement_payment` |
| A deposit must name its route | `ck_cash_movement_deposit_needs_a_route` |

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
ignore, reopen). **85 routes in the API overall.**

## Phases 4–7 · Not started

| Phase | Scope | Blocked on |
| --- | --- | --- |
| 3c — Clinic | Patients, appointments, treatment plans, service completion | C1 (clinical access rules) |
| 6 (part) — Statutory reports | Balance sheet, P&L, cash flow | A2, A3 |
| 4 — Purchasing and inventory | Suppliers, orders, receiving, supplier invoices, stock movements, payables | I1 (valuation method) |
| 5 — Employees and payroll | Employees, contracts, compensation, attendance, payroll runs, approval, posting | **P2 (current tax rules)**, P3 (commission basis) |
| 6 — Financial reporting | Balance sheet, P&L, cash flow, AR/AP ageing, dashboards | A2 (statutory forms), A3 (cash flow method) |
| 7 — Hardening | Integration and concurrency testing, security review, performance, backup and verified restore, deployment | D1 (retention policy) |

## Known gaps

Stated plainly rather than left to be discovered.

**No frontend component tests.** Vitest is installed and configured; no tests
are written. The type check and production build do run in CI.

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

## Next step

Phase 3 is now complete except for the clinic module. In order:

1. **Phase 3c — the clinic module**: patients, appointments, treatment plans
   and service completion as a business event. Blocked on `C1` (who may read
   clinical records), which decides the permission model before any screen is
   drawn. This is the last piece of Phase 3.
2. **Phase 6 (part) — statutory reports**: the balance sheet and P&L, blocked
   on `A2` and `A3`.
3. **Promote the browser passes into CI**, with the stack as a service and
   browsers cached. `frontend/e2e/banking.cjs` and `frontend/e2e/policy.cjs`
   are the first passes kept as scripts rather than done by hand; between
   them they found ten defects, and both are re-runnable.
4. **Have an accountant review the workbook-derived policy.** It now has a
   screen to review it on, every rule carries its source note, and nothing
   else in `OPEN_QUESTIONS.md` A1/A4/A5 can be settled without that review.

**Blocked until answered:** `OPEN_QUESTIONS.md` A5 (VAT — still decides
whether a revenue posting splits into net plus a tax liability), B4 (a bank
charge on a deposit or transfer), C1 (clinical access rules), S2 (the price
list).

A5 remains the one to ask first: it shapes every revenue posting, and three
modules now depend on the answer rather than one.
