# Testing

## Current state

**524 backend tests and 43 frontend tests: 522 passed, 2 skipped
deliberately, 0 failed** (the two skips are explained below). Verified counts,
from `pytest tests -q` and `vitest run`, last re-run 2026-11-01:

| Suite | Tests | Covers |
| --- | --- | --- |
| `tests/unit/test_money.py` | 34 | Decimal arithmetic, float rejection, allocation without loss |
| `tests/unit/test_permissions.py` | 23 | Permission catalogue, role definitions, segregation of duties |
| `tests/unit/test_reference_data.py` | 27 | Chart-of-accounts integrity, declared translation gaps |
| `tests/accounting/test_invariants.py` | 24 | Balance, immutability, reversal, traceability, numbering |
| `tests/accounting/test_periods_and_idempotency.py` | 16 | Closed periods, duplicate events, atomicity, **concurrent posting** |
| `tests/accounting/test_reports_and_policy.py` | 28 | Trial balance, statements, policy versioning and supersession, discarding a draft, workbook-derived default policy |
| `tests/sales/test_billing.py` | 31 | Invoicing, partial payment, over-allocation, advances, refunds |
| `tests/sales/test_debt_and_advances.py` | 22 | Patient debt and advances; the two ADR-038 ledger regressions; write-off; concurrent allocation |
| `tests/clinic/test_duplicate_review.py` | 26 | Nothing merges on matching alone; both records stay live until somebody decides; the queue carries both records; which record a merge keeps; a settled pair is not raised again; the superseded record is kept and audited (ADR-042) |
| `tests/clinic/test_payment_validation.py` | 38 | The four payment scenarios, the derived remaining balance, overpayment at service *and* database level, duplicate submission, two concurrency races, and a cash refund moving the drawer and the ledger together (ADR-040) |
| `tests/sales/test_receivables.py` | 7 | Ageing buckets, cut-off dates, payments reducing the aged amount |
| `tests/cash/test_sessions.py` | 31 | Sessions, the daily close, discrepancies, deposits, drawer-ledger agreement |
| `tests/banking/test_reconciliation.py` | 29 | Matching one-to-many and many-to-one, commission, over-matching, double counting |
| `tests/banking/test_statement_import.py` | 58 | Import tolerance, duplicate suppression, rejected rows, bank accounts |
| `tests/integration/test_phase1_slice.py` | 9 | The Phase 1 slice end to end over HTTP |
| `tests/integration/test_phase2_slice.py` | 8 | Chart → calendar → posting → trial balance, over HTTP |
| `tests/integration/test_phase3c_slice.py` | 4 | Register → treat → bill → issue → settle, over HTTP; the cashier/clinical access split; and a cash refund refused on the sales route but taken at the register (ADR-040) |
| `tests/integration/test_phase3d_slice.py` | 6 | Import → match → the books, over HTTP, including the upload itself |
| `tests/security/test_authentication.py` | 19 | Sign-in, lockout, refresh rotation, replay detection |
| `tests/security/test_authorization.py` | 18 | Least privilege, escalation attempts, SoD enforcement |
| `tests/security/test_tenant_isolation.py` | 11 | Cross-tenant access, at API, service and database level |
| `tests/security/test_audit_integrity.py` | 11 | Append-only enforcement, redaction |
| `tests/security/test_duty_separation.py` | 25 | The one-person-may-approve rule and its limits; the declaration and its audit record; closing a short till and refunding your own takings, with and without it; combined responsibilities on one person (ADR-041) |
| `tests/security/test_permission_delivery.py` | 10 | That a permission in the code reaches the database: the catalogue is declared and frozen, every system role's grants match its definition both ways, and migration 0011's frozen list matches the catalogue it delivers (ADR-043) |
| `tests/test_module_boundaries.py` | 9 | Architecture: no cycles, no reach-ins |

| Frontend suite | Tests | Covers |
| --- | --- | --- |
| `src/lib/money.test.ts` | 25 | Exact decimal arithmetic on `bigint` minor units; the four payment scenarios as the panel computes them; amounts a float would get wrong |
| `src/lib/patientIdentity.test.ts` | 18 | Comparing two patient records: what must and must not count as a difference — case, spacing and telephone formatting are not differences, a blank is *missing* rather than conflicting, and the screen shows the original spelling |

**Two tests skip, and say why.** `test_a_written_off_bill_takes_no_payment`
and `test_a_written_off_debt_owes_nothing_collectible`, both in
`tests/clinic/test_payment_validation.py`, skip while open question A7 is
unanswered: the write-off operation has no policy rule, so it fails loudly
rather than posting to a guessed account. Each skip message carries that
failure — `No rule for operation 'debt_written_off' in this policy version` —
so the tests record the state of the question rather than hiding it. Both
start passing, without being edited, on the day A7 is answered and the rule is
configured.

Frontend: TypeScript strict-mode type check, ESLint, the `vitest` suite above
and the production build all run in CI. Component tests are still not written
— see *Gaps* below.

## Running them

```bash
make test-backend                       # everything
make test-frontend                      # typecheck, vitest, build
cd backend && .venv/bin/python -m pytest tests/security -v
cd backend && .venv/bin/python -m pytest -m integration
cd backend && .venv/bin/python -m pytest --cov=app --cov-report=term-missing
```

Requires a reachable PostgreSQL and a `POSTGRES_DB` the test user may create
objects in (default `erp_test`, overridable with `TEST_POSTGRES_DB`).

## Why a real database

Most of what this system guarantees cannot be tested against SQLite:

- composite foreign keys that make a cross-tenant row unrepresentable
- `CHECK` constraints on monetary columns
- the trigger that makes `audit_log` append-only
- deferred constraint triggers (the Phase 2 balance check)
- `NUMERIC` semantics

An in-memory substitute would pass tests the real system fails. So the suite
runs against PostgreSQL in development and in CI, and the schema is built by
running the **actual Alembic migrations** — so the tests exercise the DDL that
production will receive, not a parallel definition.

## How isolation works

The schema is rebuilt once per session (`downgrade base` then `upgrade head`),
which also means migration reversibility is exercised on every run rather than
asserted once.

Each test then runs inside a connection-level transaction that is rolled back.
Services under test call `commit()`; because the session joins the outer
transaction with `join_transaction_mode="create_savepoint"`, that commit
releases a savepoint and the outer rollback still discards everything.

The session override **mirrors production's rollback-on-error behaviour**. That
detail matters: an earlier version of the fixture simply yielded the session,
and it hid a real bug where a failed sign-in's lockout counter was being
rolled back, making account lockout unreachable. A fixture that is more
forgiving than production will certify behaviour production does not have.

### The committed fixtures

Two behaviours write in their *own* transaction so they survive a failed
request — a denied-permission audit record most importantly. A separate
transaction cannot see uncommitted rows, so `committed_org` and
`committed_client` create genuinely committed data.

Those rows stay behind: `audit_log` is append-only and its organization FK is
`RESTRICT`, so neither the audit rows nor the organization can be deleted.
That is the design working as intended. Tests therefore stay scoped to their
own organization and never assume an empty table.

### Per-test engine

Each test gets its own engine with `NullPool`, and the cached global engine is
reset between tests. pytest-asyncio gives each test a fresh event loop, and a
pooled connection cannot move between loops — it surfaces as "attached to a
different loop" from somewhere unrelated. `get_engine()` selects `NullPool`
automatically when `APP_ENV=test`.

## Test data

Deterministic and fictional. Organization codes are generated with a short
random suffix so two fixtures never collide. Email addresses use
`example.com` subdomains, reserved by RFC 2606 and unable to resolve to a real
inbox.

> `.invalid` was the first choice — also RFC 2606 — but `email-validator`
> rejects special-use domains, so every form submission failed validation.
> `example.com` is equally unreachable and passes.

**No data from the reference workbook appears in any fixture.** No real name,
patient, bank account or salary. Where a test needs a shape taken from the
workbook — a card payment split into settlement and fee — the amounts are
invented and the test says so.

## What the tests actually assert

Beyond the obvious, the cases worth knowing:

**Money.** Property-style tests over awkward totals assert that
`sum(allocate(total, weights)) == quantize(total)` always holds — the property
that makes a payment allocated across invoices add back to the payment.
Passing a `float` raises. A test reads `money.py` and asserts it uses no float
helper.

**Permissions.** No shipped role may violate a segregation-of-duties control —
this failed on first run, because the `director` role had been given both
`purchasing.order.manage` and `purchasing.order.approve`. The role was
corrected, not the rule. Also asserted: only the owner holds
`accounting.policy.manage`; financial roles hold no clinical permission; the
auditor holds no write permission at all.

**Authentication.** An unknown email and a wrong password are
indistinguishable in status *and* message. A rotated refresh token presented
again revokes the entire chain, including the legitimate successor.

**Authorization.** A cashier cannot grant themselves the owner role. A forged
unsigned JWT carrying `organization.manage` is rejected at the signature. The
primary owner cannot be deactivated or demoted. Granting
`inventory_manager` + `director` is refused with the specific rule named,
while each role alone is granted successfully.

**Tenant isolation.** A valid membership id from another tenant produces the
same 404 as a nonexistent one, with the same error code. A `PATCH` carrying
another organization's id in the body edits the caller's own organization,
because the id comes from the token. A raw-SQL cross-tenant grant is rejected
by the composite foreign key.

**Audit.** `UPDATE` and `DELETE` on `audit_log` raise with "append-only". An
organization with audit history cannot be deleted. A distinctive password used
in a sign-in attempt appears in no audit value. A `Decimal` is stored as exact
text.

**Architecture.** `core` imports no module. No module imports a higher layer
or another module's `models`. No module imports `accounting.models` — a guard
written *before* Phase 2 exists, so the temptation never gets a foothold.

## The accounting invariants, and how each is proved

Every rule is tested at the service level and, where the database enforces
it, again with the service bypassed — because the point of the database
enforcement is to hold when application code does not.

| Invariant | How it is proved |
| --- | --- |
| Balanced entry posts | Two-line and three-line entries both post |
| Unbalanced entry fails | Service rejects it; then a raw-SQL line is inserted to unbalance a posted entry and the deferred trigger catches it at `SET CONSTRAINTS ALL IMMEDIATE` |
| Posted entries immutable | Raw `UPDATE` and `DELETE` on the entry and on its lines both raise; only the reversal link may change |
| Reversal offsets correctly | Lines mirror the original; both account balances net to zero; both entries are linked; the original stays visible |
| Reversal cannot repeat | A second reversal is refused |
| Closed period rejects | Service refuses; a raw insert bypassing it is refused by the trigger |
| Periods close in order | Closing out of sequence names the earliest still-open period |
| Closing refuses pending drafts | An unposted draft blocks the close and is counted in the message |
| Reopening needs a reason | Blank reason refused; the reopen is audited |
| Duplicate events do not duplicate | Second post raises; `return_existing` yields the same entry; exactly one row survives |
| Concurrent posting is safe | **Two real connections post the same event simultaneously; exactly one succeeds** |
| Structural accounts not postable | Service refuses; the `CHECK` on the trigger-filled column refuses the raw insert |
| Cross-tenant line unrepresentable | A raw insert with a foreign organization id is refused by the composite FK |
| Policy change does not rewrite history | Post under v1, activate v2 remapping the same operation, assert the posted entry is byte-for-byte unchanged |
| Effective dating resolves backwards | A superseded version still answers for a date inside its own window |
| Trial balance balances | Totals agree in every scenario, including after a reversal |
| Drafts move no balance | A draft appears in no report |
| Advance is a liability | An advance posts to a liability account and produces no revenue |

## The receivables invariants

| Invariant | How it is proved |
| --- | --- |
| A draft invoice has no ledger effect | The trial balance is empty after creating one |
| Issuing recognises revenue once | Dr 4010 / Cr 9030; collection never touches revenue again |
| A partial payment reduces the receivable | Receivable falls by the payment; revenue is unchanged |
| An advance is a liability | An unallocated payment credits 6310, and 9030 never appears |
| Applying an advance moves liability to settlement | 6310 returns to zero as the receivable clears |
| One movement is one entry | A part-advance, part-settlement payment posts a single three-line entry |
| Over-allocating a payment is refused | Service refuses; the deferred trigger refuses a raw insert |
| Over-allocating an invoice is refused | Same, both layers |
| The same payment cannot be applied twice to one invoice | Unique constraint refuses the second row |
| An issued invoice's amounts are immutable | Raw `UPDATE` on the invoice and on its lines both raise |
| A posted payment's amount is immutable | Raw `UPDATE` raises |
| Voiding reverses rather than edits | Trial balance returns to zero; the original stays visible |
| An invoice with money on it cannot be voided | Refused, naming the allocated amount |
| A refund reverses and restores the receivable | Cash returns to zero, the receivable comes back, status reverts |
| A card payment is not cash | It lands in the card settlement transit account, not 5010 |
| Prices are versioned, not overwritten | A later price change does not restate an existing invoice |
| Ageing excludes drafts and future invoices | Neither appears in the report |

## The cash invariants

| Invariant | How it is proved |
| --- | --- |
| Expected cash is derived, never stored | The schema has no such column, and adding a movement changes the figure |
| One open session per register | Service refuses; a partial unique index refuses a raw insert |
| A closed session takes no movements | Service refuses; a trigger refuses a raw insert |
| A closed session cannot be edited | Raw `UPDATE` raises |
| A difference must be explained | A count that does not match is refused without a reason |
| A shortage is an expense | Posts to 9430; revenue is untouched |
| An overage is other income | Posts to 9390, never to service revenue |
| A discrepancy needs approval | Refused without `cash.discrepancy.approve` |
| The counter cannot approve their own | Refused; another user succeeds; the owner is exempt |
| A deposit posts to transit, not the bank | 5710.01 or 5710.02 by route; the bank account stays untouched |
| A deposit cannot exceed the drawer | Refused, naming the expected balance |
| A payment cannot be counted into the drawer twice | Unique constraint on the payment |
| **The ledger equals what was counted** | After a full day and a close, account 5010 holds exactly the counted amount |

That last row is the one that matters. It is also the assertion that would
have caught the opening-float defect the first time, rather than on a
screenshot.

## The banking invariants

| Invariant | How it is proved |
| --- | --- |
| **Revenue is recognised once, not twice** | An invoice, its card payment and the settlement that follows; revenue ends at the invoiced amount |
| Importing posts nothing | The journal-entry and posting-event counts are unchanged across an import |
| A settlement clears its transit account exactly | After matching, 5110.03 is zero and 5110.01 holds what the bank reported |
| The withheld fee is recorded, not lost | 9420 holds the commission; the three postings reconcile to each other |
| A commission is cumulative on the line | A second fee on the same line does not forget the first |
| A direct transfer posts nothing on match | The bank balance is identical before and after |
| A deposit clears the route it was sent by | 5710.01 for staff, 5710.02 for collection; the other stays zero |
| A line is never marked reconciled while unexplained | A partial match leaves it UNMATCHED; the database refuses the lie either way |
| A line cannot be over-matched | Service refuses; a deferred trigger refuses a raw insert |
| A record cannot be spread over two bank lines | Refused, naming what is already matched against it |
| A matched or partly matched line cannot be set aside | Refused, naming the amount already matched |
| What the bank reported cannot be edited | Raw `UPDATE` raises |
| A match cannot be re-pointed | Raw `UPDATE` raises |
| The same statement line cannot be imported twice | The fingerprint's unique index, not the importer's in-memory set |
| A row the importer will not guess at is reported | Rejected rows come back with their line numbers and are kept on the import record |
| A statement's own totals footer is not a problem | A full export with a title block and an "Итого" line reports zero problems |
| No amount passes through a float | An Excel cell of `495000.5` arrives as `Decimal("495000.5000")` |

The first row is the module's reason for existing. A payment, a receipt, a
settlement, a statement line and a journal entry are stages of one
transaction; counting each as income would multiply turnover severalfold,
which is close to what the legacy workbook does.

Phase 4–5 invariants are listed in the original specification and will be
implemented the same way: one named test per stated rule.

## Gaps

Stated plainly:

- **Frontend coverage is one module deep.** `lib/money.ts` is tested because
  it does arithmetic on money, and arithmetic is where a float would do real
  damage. The components around it are not: the payment panel's disabling
  rules, permission-dependent rendering and the forms deserve coverage, and
  the type check, the lint and the build are all that currently stand behind
  them.
- **No load or performance testing.** Phase 7.
- **`mypy` is advisory in CI** (`continue-on-error`) until the codebase is
  fully annotated. It is not yet a gate.
- **No backup/restore rehearsal.** Phase 7, and it is a real gap: a backup
  that has never been restored is not a backup.
- **No automated browser test in CI.** The UI has been driven in a real
  Chrome and inspected screen by screen (see below). The Phase 3d pass is
  kept as a script — `frontend/e2e/banking.cjs` — and is re-runnable, but
  nothing runs it automatically. Promoting it to CI, with the stack as a
  service and browsers cached, is Phase 7.
- **Module feature lists are English in every language.** The "what is not
  available yet" banner renders the backend registry's `missing_features`
  verbatim, and those are English strings citing internal documents. Visible
  on sales, cash and banking. See `IMPLEMENTATION_STATUS.md`.

## Manual browser verification

The interface has been driven end to end in a real Chrome via Playwright and
each screen inspected, across seven passes:

* **Phase 1** — login, a rejected sign-in, the dashboard, a not-built-yet
  module page, the four settings tabs, the role dialog refusing a
  segregation-of-duties conflict, the audit trail, a permission-limited
  cashier session, and dark mode in Russian.
* **Phase 2** — fiscal year creation, the statutory chart import, a balanced
  posting, an unbalanced entry refused before it is sent, the trial balance,
  an account statement, a reversal, and a period close.
* **Phase 3** — a service with its MXIK code and price, a customer, an
  invoice raised from the price list, issuing it, a partial payment, an
  overpayment becoming an advance, the ageing report, and the trial balance
  agreeing at the end.
* **Phase 3b** — a cash register, a session, a 300 000 cash payment taken
  into the drawer, an expense paid out of it, a bank deposit, a short count
  with its reason, the close posting the shortage, and the ledger's cash
  account matching what was counted.
* **Phase 3d** — a bank account paired to its ledger account, the column
  mapping, a Cyrillic semicolon statement imported and then re-imported, a
  card settlement matched with its commission, a bank charge set aside with
  a reason and brought back, all three languages, phone and desktop widths,
  and an auditor who may read the queue but is offered no control that
  changes it. Driven by `frontend/e2e/banking.cjs`, which is re-runnable:
  each run creates its own bank account and seeds its own card payment.
* **Phase 3c** — a patient registered at reception, a second one of the same
  name and birth year reported as a possible duplicate rather than refused,
  the account showing what is owed, a payment panel whose remaining balance
  cannot be typed into, an overpayment refused by the form and then by the
  service, cash unavailable until a drawer is open and available the moment
  one is, a bill settled with part cash and part card in one submission, the
  history showing both halves, all three languages, phone and desktop widths,
  and the two role boundaries the clinic runs on: reception may register but
  not take money, the cashier may take money but not register. It checks the
  ledger afterwards — issuing recognises the revenue, taking the money does
  not recognise it again. Driven by `frontend/e2e/clinic.cjs`, which is
  re-runnable: it creates its own staff, service and patients, and banks its
  takings and closes the drawer it opened.
* **Accounting policy** — the version in force and its rules, a second
  build from the workbook refused because one is already in force, a new
  version, a rule added and another edited, the draft put into force and the
  previous version superseded rather than lost, a mistaken draft discarded,
  and an accountant who reads the policy but is offered no control that
  changes it. Driven by `frontend/e2e/policy.cjs`, which is re-runnable: it
  takes its effective date from whatever is in force and clears any draft an
  earlier run left behind.

Each run asserts there are no unexpected console errors or failed requests.
The Phase 3d and 3c scripts additionally fail on a page wider than its
viewport, which is the mechanical form of the `<select>` defect Phase 2 found
by eye.

Between them those passes found **twenty-two** defects that neither the type
checker, the linter nor the test suite could see, because they were all about
what the page actually rendered — among them a statement that could be
imported into the wrong bank account without saying so. They are listed in
`IMPLEMENTATION_STATUS.md`. The lesson worth keeping: *build and typecheck
passing is not the same as the screen being right.*

The policy pass found two defects in code that had been passing its tests
since Phase 2 — an activation that threw a 500 instead of refusing, and a
draft version that could neither be activated nor removed. Both now have
tests. Neither was reachable until the screen existed, which is the argument
for building the screen.

The Phase 3c pass found three, all in what the payment panel showed rather
than in what it did: a remaining balance of "0" while the form held more than
was owed, a payment form laid out in full for somebody who may not submit one,
and the same sentence about the cash drawer printed twice on one screen. The
first is the one that mattered — the arithmetic underneath was right, and the
figure on the screen said the opposite.

Three of the Phase 3d defects were in shared components — `Card`,
`CardHeader`, `PageHeader` — so fixing them straightened the layout of every
screen, not just banking's. Measured afterwards across ten screens, three
languages and five widths: nothing overflows its viewport anywhere, which was
not true before.

## Definition of done

A feature is complete when its business rules are documented, its migration
works, its endpoints work, its frontend workflow works, backend authorization
is enforced, validation and errors are handled, the accounting effects are
correct, the audit events are recorded, the tests pass, and it can be
demonstrated with fictional data.

`IMPLEMENTATION_STATUS.md` tracks every feature against those ten points, and
a feature that misses one is not reported as done.
