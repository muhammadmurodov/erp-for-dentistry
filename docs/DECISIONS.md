# Architectural decisions

Settled decisions and why. Anything *unsettled* is in `OPEN_QUESTIONS.md`.

Format: context, decision, consequences — including the ones we dislike.

---

## ADR-001 — Modular monolith, not microservices

**Context.** The specification asks for 13 bounded modules.

**Decision.** One deployable backend with enforced internal boundaries.

**Why.** There is no scaling pressure: the clinic generates roughly a thousand
fiscal receipts a year. Splitting a double-entry ledger across a network
replaces a database transaction with a distributed-consistency problem, and
the invariant at stake is "debits equal credits". Module boundaries get the
benefit of separation without that cost.

**Consequences.** Boundaries must be enforced by something other than the
network. `tests/test_module_boundaries.py` checks the dependency graph for
cycles, upward edges and cross-module model imports. A module can be extracted
later if a reason appears; none has.

---

## ADR-002 — `Decimal` for money, and `float` raises

**Context.** Monetary correctness is the project's first priority.

**Decision.** `decimal.Decimal` in memory, `NUMERIC(20, 4)` in the database,
`ROUND_HALF_UP`. `to_amount()` raises `ValidationFailed` when handed a
`float`.

**Why.** Rejecting floats at the type boundary is better than documenting that
they should not be used. A float that enters the system is found at the point
of entry, not in a trial balance that is off by 0.01.

**Consequences.** Callers must pass `Decimal`, `int` or `str`. Slightly more
ceremony at the edges; the frontend formats amounts as strings and never
converts them to `number`, so large som values cannot be corrupted by
JavaScript's 53-bit floats either.

---

## ADR-003 — Four stored decimal places, two for UZS display

**Context.** UZS has two statutory minor units (tiyin) but coins are out of
circulation; VAT extraction and commission percentages produce more places.

**Decision.** Store `NUMERIC(20, 4)`. Quantize to the currency's
`decimal_places` (2 for UZS) before posting.

**Why.** Intermediate values need headroom; posted values need a canonical
form. The legacy workbook already holds amounts like `17 020.65`, so two
places is what the existing books use.

**Consequences.** `currency.decimal_places` is the rounding authority, and a
new currency needs a row rather than a code change.

---

## ADR-004 — Authority attaches to a membership, not a user

**Context.** An accountant may serve several organizations.

**Decision.** `Membership` is the (user, organization) pair and carries all
role grants. `User` carries none.

**Why.** It makes "accountant here, auditor there" expressible, and a
cross-organization privilege *inexpressible*. The alternative — roles on the
user with an organization column — permits a row that grants authority
everywhere.

**Consequences.** Every permission check needs a resolved membership. A
superuser without a membership cannot touch a tenant's books, which is
intended: platform administration and organization authority are different
things.

---

## ADR-005 — Composite foreign keys for tenant isolation

**Context.** Cross-tenant data access must be impossible, not merely
prevented.

**Decision.** Every scoped table carries `UNIQUE (organization_id, id)`.
Children reference `(organization_id, parent_id)`.

**Why.** It moves the guarantee from code into the schema. A child whose
organization differs from its parent's is unrepresentable, not "rejected by a
check somebody might forget".

**Consequences.** One redundant-looking unique constraint per table. Accepted
cheerfully: it is what makes accounting invariant 3 structural. Already
guarding `membership_role`; Phase 2 applies it to `journal_line`.

---

## ADR-006 — Explicit query scoping, not row-level security

**Context.** PostgreSQL RLS could enforce tenancy centrally.

**Decision.** Explicit `scoped()` filtering plus composite foreign keys. RLS
is not used, for now.

**Why.** RLS needs a session variable set on every connection, which is
fragile under connection pooling — a pooled connection carrying the previous
request's tenant is a cross-tenant read, and that failure is silent. Explicit
scoping is visible in the query and directly testable.

**Consequences.** Every scoped read must call `scoped()`. Discipline, enforced
by tests rather than by the engine. RLS stays a candidate as defence in depth
once the schema settles.

---

## ADR-007 — Permissions embedded in a short-lived access token

**Context.** Resolving permissions needs several joins.

**Decision.** Embed the resolved set in a 15-minute JWT. Refresh tokens are
opaque and rotate.

**Why.** It avoids a role query per request. The cost — a revoked role stays
effective until expiry — is bounded by the short TTL, and anything that must
take effect at once also revokes the refresh chain.

**Consequences.** A role change takes up to one access-token lifetime to bite.
Documented in `SECURITY.md`. If that ever becomes unacceptable, the fix is a
revocation list in Redis, not a longer-lived decision.

---

## ADR-008 — Audit rows join the caller's transaction, with two exceptions

**Context.** Should an audit write be independent of the work it describes?

**Decision.** Same transaction by default. Two exceptions: a denied permission
writes in its own transaction; a rejected sign-in is committed by the auth
router before the error propagates.

**Why.** If a posting rolls back, its "posted" audit record must roll back
too — a trail claiming things happened which did not is worse than none. But
a denial and a failed sign-in belong to requests that are *designed* to fail,
and must survive anyway.

**Consequences.** Two code paths. The sign-in path also persists the
failed-attempt counter — without which lockout would never trigger, which was
a real bug caught by a test. An independent audit write can fail silently
(logged, swallowed) so that it never masks the original error.

---

## ADR-009 — The audit trail is append-only by trigger

**Context.** "Protect audit records from ordinary user modification."

**Decision.** A `BEFORE UPDATE OR DELETE` trigger on `audit_log` that always
raises.

**Why.** Revoking `UPDATE`/`DELETE` would not help: the application typically
owns its own schema. A trigger holds regardless of privileges.

**Consequences.** An organization with audit history cannot be deleted
(`ON DELETE RESTRICT`), so `seed-demo` is idempotent-by-skipping with no
`--force` — there is nothing honest `--force` could do. Tests that exercise
independent audit writes leave rows behind, so tests stay organization-scoped
and never assume an empty table.

---

## ADR-010 — Relationships are `lazy="raise_on_sql"`

**Context.** An implicit lazy load in async SQLAlchemy raises
`MissingGreenlet` far from its cause. This cost real debugging time during
Phase 1.

**Decision.** Declare relationships `lazy="raise_on_sql"` and load explicitly.

**Why.** It converts a confusing runtime failure into a loud local error, and
forces every read to state how it loads its data — which also removes a class
of N+1 queries.

**Consequences.** More `selectinload()` calls, and some reads query the link
table directly instead of walking a relationship (a freshly-created object has
no loaded collection to walk).

---

## ADR-011 — A policy change never rewrites history

**Context.** The legacy workbook derives each entry's Dr/Cr accounts with an
`INDEX/MATCH` formula against the policy sheet. Editing one policy row
silently changes what every historical entry appears to say.

**Decision.** A posted entry stores the **resolved account ids** and the
policy version that produced them. Policies are versioned and effective-dated;
a change affects future postings only.

**Why.** This is the most serious defect in current practice. Books that can
be retroactively altered by a cell edit are not books.

**Consequences.** More storage per entry and a version table. Trivially worth
it.

---

## ADR-012 — Corrections are reversals, never edits

**Context.** Should an entry be editable while its period is open?

**Decision.** No. A posted entry is immutable. Corrections reverse and
replace.

**Why.** "No statutory report has been filed yet" is a judgement the system
cannot verify. Once edits are possible the audit trail stops being evidence.

**Consequences.** Two entries per correction and a longer ledger. The
immutability trigger means there is no alternative path to try.

---

## ADR-013 — Indirect cash flow method

**Context.** Both methods are defensible.

**Decision.** Indirect, with the method named on the report.

**Why.** It derives entirely from the ledger. The direct method needs every
cash movement classified as operating / investing / financing at entry time,
which the clinic does not record and which needs an accountant's rules first.

**Consequences.** Revisitable; `OPEN_QUESTIONS.md` A3 asks whether statutory
filing requires the direct method.

---

## ADR-014 — No historical data import

**Context.** The workbook holds nine years of records.

**Decision.** Open the new books with an opening-balance journal entry at a
cut-over date. Keep the workbook as the historical record.

**Why.** Three reasons. The opening balances depend on classification
decisions that are still open. The workbook's Dr/Cr columns are formulas, so
historical postings are not stable values. And part of the bookkeeping is in a
physical ledger that was unavailable, so the workbook is known to be
incomplete.

**Consequences.** Needs an accountant-signed trial balance at cut-over
(`OPEN_QUESTIONS.md` A6). Importing fiscal receipt history for analysis is
separable and may follow.

---

## ADR-015 — Module implementation status comes from the backend

**Context.** "Do not hide unfinished functionality behind a polished-looking
interface."

**Decision.** A registry in the backend records each module's real status.
`GET /platform/modules` serves it; the navigation marks unfinished modules and
their pages list what is and is not built. The frontend may not hard-code it.

**Why.** A polished screen whose buttons do nothing misleads the people
deciding whether to run their business on this. Serving status from one place
makes honesty the default rather than a thing to remember.

**Consequences.** The registry must be updated in the same change that
completes a module. An integration test asserts nothing beyond Phase 1 reports
itself `available`.

---

## ADR-016 — No statutory rate is hardcoded, ever

**Context.** The workbook contains 2018 payroll rates and a VBA income-tax
function, and the tax regime demonstrably changed between 2021 and 2025.

**Decision.** Statutory rules are effective-dated, versioned database records
supplied by the accountant. None is carried over from the workbook, and the
VBA was never executed.

**Why.** A hardcoded rate is wrong the day it changes, and silently wrong. A
recalculation of an earlier period must use the rules that applied then, which
requires versions with date ranges regardless.

**Consequences.** Payroll cannot run until the rates are supplied
(`OPEN_QUESTIONS.md` P2). That is the correct state: computing payroll with
invented rates would produce confident, filed, wrong numbers.

---

## ADR-017 — Statutory reference data is flagged unconfirmed

**Context.** 368 accounts were extracted from the workbook, but its hierarchy
column is incomplete, so part and element are derived from code ranges.

**Decision.** Ship the data with every row flagged `needs_confirmation`. The
loader refuses a file that has lost its provenance notice. Cross-check the
derivation against accounts the workbook actually posts to, as a test.

**Why.** Derived data that presents itself as authoritative is worse than
absent data. The flag and the notice keep its status attached to it.

**Consequences.** Phase 2 marks seeded accounts provisional until signed off.
The translation-gap check in the timesheet data follows the same pattern: 12
codes lack Uzbek text, the gap is declared, and a test fails if it changes
without being re-declared.

---

## ADR-018 — The owner role is exempt from segregation of duties

**Context.** Five SoD pairs are enforced. A single-owner MChJ has one person.

**Decision.** The `owner` role may hold conflicting pairs. No other role may,
and the exemption is explicit in code and tested.

**Why.** Blocking it would make the system unusable for its actual first
customer. The audit trail is the compensating control: the owner's approval of
their own request is recorded as two distinct acts.

**Consequences.** An owner can self-approve. Visible in the audit trail, and
the exemption is deliberate rather than a gap. This surfaced when defining the
`director` role, which had accidentally been given both purchase-order
permissions — the check failed the build and the role was corrected.

---

## ADR-019 — Tenant provisioning is its own module

**Context.** Creating an organization and its first owner spans
`organization` and `identity`. Putting it in either created a dependency
cycle, which the boundary test caught.

**Decision.** A `provisioning` module above both, whose only job is to compose
them in one transaction.

**Why.** The alternative was an exception in the boundary rules, and an
exception would have been the first of several.

**Consequences.** One more module. This is the pattern to reuse whenever a use
case spans modules — Phase 3's treatment → invoice → payment → posting flow
will need it.

---

## ADR-020 — The test database is real PostgreSQL

**Context.** SQLite would be faster and need no service.

**Decision.** Real PostgreSQL, with the schema built by the actual migrations.

**Why.** Composite foreign keys, check constraints, deferred constraint
triggers, the append-only trigger and `NUMERIC` semantics do not exist in
SQLite. An in-memory substitute would pass tests the real system fails.

**Consequences.** CI needs a service container. The suite runs in about 11
seconds, so the cost is negligible — and migration reversibility gets
exercised on every run, because the fixture rebuilds the schema by downgrading
to base and upgrading again.

---

## ADR-021 — Gapless entry numbers from a locked row, not a sequence

**Context.** Journal entries need unique, traceable numbers (invariant 7).

**Decision.** A `number_sequence` row per organization, taken under
`SELECT ... FOR UPDATE`.

**Why.** A PostgreSQL sequence is not transactional: a rolled-back posting
burns a number and leaves a gap. An accountant reading a ledger with missing
entry numbers has to prove nothing was deleted, and "the software does that"
is a poor answer during an audit. At a few thousand entries a year the lock
costs nothing.

**Consequences.** Concurrent postings serialise on that row. If volume ever
makes this a bottleneck, the fix is per-period sequences, not a non-gapless
one.

---

## ADR-022 — `account_is_postable` is filled by a trigger, never by the caller

**Context.** A journal line must not reference a part, section or group — a
structural account aggregates its children, so posting to one double-counts
it in a trial balance.

**Decision.** `journal_line.account_is_postable` mirrors the account, is set
by a `BEFORE INSERT` trigger, participates in the composite foreign key to
`account (organization_id, id, is_postable)`, and carries a `CHECK` requiring
it to be true.

**Why.** The first attempt had the application supply the value, which meant
a caller could assert postability an account did not have — the foreign key
caught it, but with an opaque message. Deriving it in the database makes the
bad row unrepresentable *and* produces a readable error.

**Consequences.** Three mechanisms for one rule. Deliberate: the trigger
derives, the foreign key relates, the check refuses.

---

## ADR-023 — A superseded policy still answers for its own dates

**Context.** `policy_in_force_on(date)` originally matched only `ACTIVE`
versions. A test posting back into a period covered by a superseded version
then failed to resolve any policy at all.

**Decision.** Resolution matches on the effective-date window and excludes
only `DRAFT`.

**Why.** It is the entire point of effective dating. A back-dated posting, or
a recalculation of an earlier period, must use the rules that applied then.
"Active" describes which version applies *today*, not which applied in June.

**Consequences.** A version is never pruned; `effective_to` is closed the day
before its successor starts, so exactly one version answers for any date.

---

## ADR-024 — One mechanism owns the line-status cascade

**Context.** Promoting a draft set `entry_status` on each line from Python
*and* via a database trigger. The second write arrived after the first and was
correctly rejected by the immutability guard.

**Decision.** The trigger owns it. The service flips the entry and expires the
loaded line objects.

**Why.** Two mechanisms maintaining one invariant will disagree, and the one
that notices is whichever runs second. The database is the better owner here
because the guard it collided with is also in the database.

**Consequences.** `post_draft` must expire its loaded lines so later reads see
the new value. Noted in the method.

---

## ADR-025 — Monetary values cross HTTP as strings

**Context.** JSON numbers are IEEE-754 doubles in every browser.

**Decision.** Every monetary field is serialised as a decimal string, and the
API rejects a JSON number for an amount outright. The frontend formats
strings and sums them with integer arithmetic on minor units.

**Why.** A som amount above 2^53 minor units would silently lose precision,
and a quietly wrong figure is worse than a refused request. Rejecting the
float at the edge matches `to_amount()` rejecting it in Python (ADR-002).

**Consequences.** Clients must send strings. The frontend carries a small
decimal-string adder rather than reaching for `parseFloat`.

---

## ADR-026 — An invoice's balance is never a stored column

**Context.** An invoice's outstanding amount could be a field kept in step by
the code that records payments.

**Decision.** `PaymentAllocation` rows are the only record. Outstanding is
`total - sum(allocations)`, computed where it is needed. Invoice status
(`ISSUED` / `PARTIALLY_PAID` / `PAID`) is likewise derived from those rows
rather than assigned.

**Why.** The same reason a trial balance is not a stored total: a cached
field and the rows it summarises will disagree eventually, and then nobody
knows which is right. This also makes "one payment across several invoices"
and "several payments against one invoice" fall out for free.

**Consequences.** A sum per read. At a clinic's volume that is nothing, and
the sums are indexed. If it ever matters, the fix is a materialised view, not
a hand-maintained column.

---

## ADR-027 — Over-allocation is prevented by a lock *and* a deferred trigger

**Context.** The specification's named hazard: two cashiers apply the same
payment at the same moment.

**Decision.** Three mechanisms. `SELECT ... FOR UPDATE` on both the payment
and the invoice before the sums are checked; a unique constraint on
`(payment, invoice)`; and a `DEFERRABLE` constraint trigger that re-checks
both totals at `COMMIT`.

**Why.** The lock serialises the check-then-act. The unique constraint stops
a duplicated request settling the same invoice twice. The trigger holds even
if something bypasses the service entirely. Each covers a case the others do
not.

**Consequences.** Three places to understand. The test suite exercises all
three, including by raw SQL with the service bypassed.

---

## ADR-028 — The tax treatment of revenue must be stated, never defaulted

**Context.** Issuing an invoice needs to know whether the price includes VAT.
The clinic's fiscal receipts all report zero VAT; its 2025 bank statement
shows VAT being paid. The evidence contradicts itself
(`OPEN_QUESTIONS.md` A5).

**Decision.** `issue_invoice` takes an explicit `tax_treatment`.
`UNDECLARED` is refused with a message naming the open question.
`VAT_INCLUSIVE` is refused as not implemented, because the rate and the
applicability are both unconfirmed. Only `NONE` — gross posting, matching
every receipt on file — actually works today.

**Why.** Guessing either way is a real financial error: inventing a VAT
liability that does not exist, or understating one that does. A refusal that
names the question is more useful than a default that hides it.

**Consequences.** The sales module reports `partial`, and its page lists
VAT-inclusive pricing as missing. When an accountant answers, implementing
`VAT_INCLUSIVE` is a contained change — the arithmetic is already written and
tested in `app/core/money.py`.

---

## ADR-029 — The default policy is derived from the workbook as a draft

**Context.** Posting needs an accounting policy, and the clinic's workbook
already maps operations to Dr/Cr pairs.

**Decision.** `seed-default` builds a **draft** policy from those mappings.
Each rule records where in the workbook it came from. Rules the workbook's
own author flagged as doubtful are excluded. Seeding is refused when a policy
is already in force.

**Why.** Transcribing the sheet into an active policy would launder an
unreviewed accounting rule into authority. A draft with provenance is a
starting point for an accountant; an active policy is a claim.

**Consequences.** An organization cannot post until somebody activates the
policy, which is audited. That is the intended friction.

---

## ADR-030 — The cash and banking modules sit *above* sales

**Context.** A cash session contains payments; a bank settlement matches
them. Either direction of dependency is expressible.

**Decision.** `sales` owns invoices and payments and knows nothing about cash
sessions or bank statements. `cash` and `banking` sit a layer above and
reference payments.

**Why.** The container knows its contents, not the reverse. It also keeps a
payment recordable without a cash session, which matters because card and
bank payments never involve one.

**Consequences.** Recording a cash receipt will be orchestrated by the cash
module calling sales, the same shape as `provisioning` (ADR-019).

---

## ADR-031 — An opening float must already be in the ledger

**Context.** A cash session's opening float counts toward expected cash but
posts nothing — the money is already in the drawer. Driving a full cash day
in a browser showed what that assumption costs when it is not checked: the
till expected 135 000 while the ledger's cash account held 80 000, differing
by exactly the 50 000 float. Two figures for the same cash, disagreeing
silently.

**Decision.** Opening a session validates the float:

* after a previous session, it must equal that session's counted amount —
  the cash did not move overnight;
* on a register's first session, it must equal the ledger balance of the
  register's account. A larger float is an *opening balance*, which needs a
  journal entry (OPEN_QUESTIONS A6), not a number typed into a till.

**Why.** A reconciliation whose two sides are not derived from the same
reality is theatre. This makes "expected cash" and "ledger cash" agree by
construction rather than by hope.

**Consequences.** A clinic cannot start using a till with cash already in it
until an opening balance is posted. That is the correct friction: that cash
came from somewhere, and the books should say where.

The guarantee now has its own test — after a full day of movements and a
close, account 5010 holds exactly what a person counted.

---

## ADR-032 — Money is formatted for display in one place

**Context.** Monetary columns are `NUMERIC(20, 4)` for intermediate headroom
(ADR-003), but every stored amount is quantized to its currency first. The
padding zeros leaked to the screen as `130000.0000` three separate times —
once per module — each fixed with another per-field `quantize` call.

**Decision.** `app.core.money.for_display` strips the storage padding
losslessly, and a single shared `MoneyMixin` in `app.core.schemas` applies it
to every `Decimal` field on the way out. All three schema modules use it.

**Why.** The third occurrence of a bug is the signal that the fix belongs
somewhere else. `normalize()` only removes trailing zeros, so the helper
cannot change a value; a currency with more decimal places keeps them.

**Consequences.** A nil balance now serialises as `"0.00"` rather than `"0"`,
which is what money should look like, and one integration expectation was
updated to match.

---

## ADR-033 — Matching records a fact; it posts only where an entry is due

**Context.** A patient payment, a fiscal receipt, a card settlement, a bank
statement line and a journal entry are five records of **one** economic
transaction. The clinic's workbook treats the settlement as income, which
both loses the acquirer's fee and — were the payment also recorded — would
count the same money twice.

**Decision.** A reconciliation match is a *link*, stored in
`reconciliation_match`, between a statement line and records the system
already holds. Matching posts an entry only where one is genuinely due:

| What is matched | What posts |
| --- | --- |
| A card payment | Dr bank / Cr card transit, for the **net** |
| The withheld fee | Dr 9420 / Cr card transit |
| A cash deposit | Dr bank / Cr the route's transit account |
| A direct bank transfer | **nothing** |

**Why.** Each posting moves money *out of a transit account*, which is where
the earlier stage put it. Nothing recognises revenue, because revenue was
recognised when the service was invoiced. A transfer the customer sent
already debited the bank account when the payment was recorded, so
confirming it again would double the bank balance.

**Consequences.** After a settlement is matched, the transit account is
exactly zero and the bank account holds exactly what the statement says —
which is the whole point of reconciling. A non-zero transit balance is
precisely the list of receipts the bank has not confirmed yet.

The invariant is also enforced in the database
(`assert_reconciliation_is_coherent`): what is matched to a line, net of the
fee, can never exceed the line, and a line may call itself reconciled only
when the two are equal. A bug in the service cannot inflate turnover.

---

## ADR-034 — A statement line is evidence, and evidence is immutable

**Context.** Reconciliation is an argument that two independently produced
records agree. If either side can be edited to make them agree, the argument
proves nothing.

**Decision.** `trg_bank_transaction_evidence_immutable` refuses any change to
what the bank reported — the account, value date, direction, amount or
fingerprint. The business may still decide what a line *means*: its status,
the fee declared against it, and the reason it was set aside.
`trg_reconciliation_match_immutable` likewise refuses to re-point or re-value
a match; one is removed and made again instead.

A line is identified by `fingerprint_for` — a digest of value date,
direction, amount, document number and normalised description. The running
balance and the row number are deliberately excluded: both shift when the
exported date range changes.

**Why.** Re-downloading a statement is routine, and an importer that creates
a second copy of every line each time is worse than useless. The digest is
unique per bank account, so two clinics banking with the same bank — or one
clinic with two accounts — never suppress each other's lines.

**Consequences.** A mis-mapped import is corrected by re-importing, not by
editing rows. Importing the same file twice reports every line as already
held and changes nothing.

---

## ADR-035 — The statement's shape is discovered, not assumed

**Context.** The bank's export format is open question B3 — unanswered. An
importer written against one sample file fails on the first statement anybody
actually downloads.

**Decision.** Three things are discovered rather than configured:

* **The delimiter.** `csv.Sniffer` inspects the opening characters, and a
  statement opens with a title block, the account holder and a blank line
  rather than a table — so it reports that it cannot tell, and the fallback
  to a comma reads a semicolon-separated file as one very wide column. Each
  candidate separator is tried instead, and the one producing the widest
  table that most rows agree on wins.
* **The header row.** Searched for in the first 30 rows, by looking for a
  date column and an amount column rather than by position.
* **The decimal convention.** A comma is the decimal mark when there is no
  dot and a thousands separator when there is; spaces, non-breaking spaces,
  narrow no-break spaces and apostrophes are all grouping characters. No
  amount ever passes through a binary float, including the ones openpyxl
  hands back as `float` (ADR-002).

What is *configuration* is the column mapping: a `column_mapping` on the bank
account maps this system's field names onto that bank's headings, so
supporting a different bank is a settings change rather than a release.

**Why.** Tolerance is cheap to build and expensive to retrofit. The
alternative is a release for every bank.

**Consequences.** A row the importer will not guess at is **reported**, with
its line number, and the import record keeps the list — an import that
silently dropped a third of its lines is the kind of thing that surfaces at
year end. The one exception is the statement's own totals footer, recognised
by label in three languages: reporting it as a problem on every single import
is how a problem list gets ignored.

---

## ADR-036 — Banking reads payments and deposits through their services

**Context.** Reconciliation needs to know what a payment is worth and which
transit account a deposit went to. Both records belong to other modules.

**Decision.** `banking` calls `BillingService.list_settleable_payments`,
`BillingService.get_payment` and `CashSessionService.list_deposits` /
`get_deposit`. It does not import `sales.models` or `cash.models`, which the
boundary test refuses outright.

**Why.** "Which payments are still awaiting money from a third party" is a
receivables question and "only a deposit may be confirmed against a bank
line" is a cash question. Answering them inside `banking` would put the same
rule in two places, and the second copy is the one that goes stale.

**Consequences.** Two read methods were added to the owning services. The
layering in ADR-030 is unchanged: `banking` still sits above both.

---

## ADR-037 — A draft policy version can be discarded; an active one cannot

**Context.** A policy version's effective date is fixed at creation — there is
no endpoint to change it, deliberately, because an effective date that moves
would make "which rules applied on this date" unanswerable. But a draft also
*blocks*: `create_draft` refuses while an unactivated draft exists, so one
created with the wrong date could neither be activated (it would start before
the version in force, which `activate` now refuses) nor replaced. The only
way out was `psql`. Found by building the policy screen and using it.

**Decision.** `DELETE /accounting/policies/{id}` discards a draft and its
rules. An `ACTIVE` or `SUPERSEDED` version is refused, and told to supersede
with a new version instead.

**Why.** A draft has posted nothing: no journal entry names it, no posting
event resolved through it, so removing it leaves no gap in the ledger and
nothing to reconcile. That is exactly the reasoning that already lets a draft
journal entry be deleted while a posted one must be reversed (ADR-012) — the
same rule, applied to the same kind of object.

An active version is different in kind. Entries posted under it record which
version chose their accounts, and a superseded version still answers for its
own dates (ADR-023). Deleting either would leave posted entries pointing at a
policy that no longer exists.

**Consequences.** The discard is audited *before* the row is deleted, with the
version number, effective date and rule count, so the record of what was
thrown away outlives the thing itself. The policy editor is no longer a dead
end: a mistaken draft costs one click rather than a database session.
