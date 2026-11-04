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

---

## ADR-038 — An allocation is reversed, never deleted

**Context.** `remove_allocation()` deleted the `payment_allocation` row and
posted nothing. For an allocation created when a payment was recorded that
was almost right: the payment's own entry carried both sides, so there was no
separate entry to unwind. For an **advance applied later** it was wrong. That
path posts an entry of its own — `6310 Dr / 4010 Cr`, a liability becoming a
settled receivable — and deleting the row left that entry standing with
nothing to explain it.

Driving the real services and reading the trial balance afterwards showed what
that costs. After removing such an allocation the subledger reported the
invoice outstanding at 300 000 while ledger account 4010 reported zero: the
receivable had become invisible in the accounts. **The trial balance still
balanced**, which is why no existing test caught it — internal balance is not
correctness.

Refunding the same payment was worse. `refund_payment()` looked for entries by
`(source_kind=PAYMENT, source_id=payment, purpose=PAYMENT)`, and the
advance-application entry is posted with `purpose=ADJUSTMENT` under a
different source id, so the lookup could not match it. It was left standing
while the payment entry was reversed, and account 6310 — a liability — ended
up carrying a **debit balance of 300 000**. A negative liability is not a
small error; it is a statement that means nothing. The allocation rows had
been deleted too, so the corruption could not even be traced.

**Decision.** An allocation is marked reversed, not deleted:
`reversed_at`, `reversed_by_user_id` and a mandatory `reversal_reason`. Any
entry the allocation posted is reversed with it, in the same transaction.
`refund_payment` reverses every entry derived from the payment — its own and
each of its allocations' — before reversing the payment itself, and keeps the
allocation rows.

Three supporting changes fall out of it:

* The advance-application entry is posted under the **allocation's own id**
  rather than the `payment:invoice` pair. The pair is not unique over time:
  once an allocation can be reversed, the same payment may legitimately be
  applied to the same invoice again, and the idempotency guard would have
  refused the correction as a duplicate.
* The uniqueness rule becomes a **partial unique index over live rows**
  (`WHERE reversed_at IS NULL`), so the same payment can be re-applied after
  a reversal while a double-click still cannot settle an invoice twice.
* Every sum that means money counts live rows only. `list_allocations` takes
  `include_reversed`, and only a history view passes it.

**Why not fix the lookup and keep deleting?** Because the deletion is the part
that cannot be recovered from. An entry that was not reversed can be reversed
later; an allocation row that was deleted takes with it the only record that
a patient's money was once applied to a particular bill. The specification
asks for history to survive a correction, and a correction that erases its own
evidence is the thing being guarded against.

**Consequences.** `POST /sales/allocations/{id}/remove` became
`/reverse` and requires a reason. Two regression tests assert the ledger and
the subledger agree after a reversal and after a refund, which is the
assertion neither had before.

---

## ADR-039 — Settling a bill is one operation, and the remaining balance is derived

**Context.** A patient paying 400 000 in cash and 600 000 by card has made one
decision and expects one receipt. Underneath, the two halves genuinely differ:
cash goes into a drawer that gets counted, card money waits in transit until
the bank settles it. Recording them as two independent payments left two
problems. Each was checked against the remaining balance on its own, so
600 000 and 600 000 both passed on a bill of 1 000 000; and nothing tied them
together, so a client retrying after a timeout could take the second half
twice.

A third problem was the balance itself. The figure a cashier needs — what is
still owed — existed only as `invoice.total − sum(allocations)` computed at
various call sites, with no single read model and no statement anywhere about
who is allowed to decide it.

**Decision.** `SettlementService` in the **clinic** module composes one
settlement: apply advances, take cash, take card, in that order, inside the
invoice's row lock, after a single check of the whole submission against the
remaining balance. Its `view()` is the one read model for the panel —
treatment total, confirmed payments split by method, advance available,
remaining balance, and a derived `SettlementStatus`.

It lives in `clinic` because `clinic` is the only module above both `sales`
and `cash` (ADR-030), so it can reach a receivable and a drawer in one
transaction without either acquiring a dependency on the other. It owns no
money: every amount is read from `BillingService` and every entry is posted
by it. The one table it adds, `settlement_request`, holds a client-supplied
`request_key` unique per organization, plus the amounts submitted and the ids
of the payments they produced — so a resubmission is answered with the first
outcome instead of taking the money again, and a key reused for *different*
amounts is refused rather than replayed.

**The remaining balance is never an input.** `SettlementRequest` has no field
for it, at any level of payment: there is nowhere for an edited balance to
arrive. The frontend renders it into a disabled input computed from the
invoice and the draft amounts in exact decimal arithmetic on `bigint` minor
units — never `number`, which is an IEEE-754 double and would lose precision
on a large som amount. The panel's disabling of fields is a courtesy to the
cashier, not a control: the backend recomputes everything under the lock.

**Lock order is fixed: invoice first, then payment.** `allocate()` previously
took them the other way round, which would have deadlocked against a
composite settlement holding the invoice. Every path now agrees, including the
database trigger.

**The database enforces it too.** Migration 0003 created
`assert_allocation_within_limits` as a deferred constraint trigger summing
every allocation row. ADR-038 then made allocations reversible without
teaching the guard about it, so the guard counted money that had been taken
back off — and a legitimate re-application after a reversal would have been
refused at COMMIT. The tests did not catch it because they run inside a
transaction that is rolled back, and a deferred trigger never fires without a
commit. Migration 0008 rewrites it: live rows only, scoped by organization,
with the same row locks the service takes, and no longer deferred, so the
error names the statement that caused it.

**Consequences.** `POST /clinic/invoices/{id}/settlement` is the one way to
take a patient's money, and `GET` on the same path is the one way to ask what
is owed. Cash is still refused on the generic sales payment route (C-3), so
there is exactly one path that puts cash in a drawer and in the ledger
together. Thirty-eight backend cases cover the four payment scenarios,
advance application, overpayment, duplicate submission and two concurrency
races; twenty-five frontend cases cover the arithmetic, including amounts a
float would get wrong; and an HTTP slice runs the whole thing from registering
a patient to the trial balance.

That slice earned its place immediately. Both settlement routes built their
response with `vars()`, which raises on a `slots=True` dataclass, so each
returned a 500 while every service-level test passed. A service that works
behind a route that does not is not a working system, and only a request
finds that out.

## ADR-040 — Giving cash back is a drawer movement, not only a ledger reversal

**Context.** `cash_movement` carried a unique constraint on
`(organization_id, payment_id)`. The rule it was reaching for is right — a
payment must not be counted into a drawer twice — but it was stated too
broadly, because it also made it impossible to record that cash had been paid
back *out* for a payment that already had a receipt row: the second row
collided with the first.

So refunding a cash payment did half the job. `BillingService.refund_payment`
reversed every entry the payment had produced, and account 5010 went back to
zero, but no movement was recorded against the session. The drawer went on
expecting money that had been handed across the counter. At the end of the
day the expected balance stood at the full amount, the physical count came up
short by exactly the refund, and the close posted real cash to 9430 as an
operating loss — a variance invented by the system, against a till where
nothing had gone wrong.

It is the same defect as taking cash with no session open (C-3), in the other
direction, and it was reachable through the only path that handles cash at
all. It was found by probing the settlement paths against the real services
and reading the session afterwards, not by a failing test: every test that
touched a refund asserted the ledger, and the ledger was correct.

**Decision.** The constraint becomes
`(organization_id, payment_id, kind)` — `uq_cash_movement_payment_kind`,
migration `0009`. One receipt and one refund per payment, each still
impossible to duplicate, and the broader rule is not weakened in the
direction it was written for. Every existing row satisfies the narrower form
unchanged: a payment with one movement cannot violate a uniqueness rule that
has only become more specific.

`CashSessionService.record_payment_refund` mirrors `record_payment_receipt`.
It writes a `MovementKind.REFUND` row, which is in `OUTFLOW_KINDS`, so the
expected balance falls by the amount — amounts stay positive and the kind
carries the direction, as everywhere else in this module. It posts
**no** ledger entry: `sales` has already reversed the original, and posting
again here would double the reversal.

**The drawer is the one open now, not the one that took the money.** A refund
given the next day comes out of the till that is open today, because that is
where the cash physically comes from. Tying it to the original session would
either refuse the refund or move cash out of a drawer that had been counted
and closed.

**Two routes, one for each direction, and neither will do the other's work.**
`POST /cash/sessions/{id}/refunds` gives cash back: it requires
`sales.refund.approve`, checks the session is open *before* reversing
anything, and refuses a payment that was not taken in cash.
`POST /sales/payments/{id}/refund` now refuses a cash payment outright and
names the cash screen in the message — the mirror of C-3, where cash cannot
be taken on the generic sales route either. There is exactly one path that
moves cash in each direction, and both move the drawer and the ledger in the
same transaction.

**Consequences.** `0009`'s downgrade fails loudly if a refund movement exists,
rather than dropping the row to satisfy the old constraint: losing the record
that cash left a till is worse than a migration that refuses to run.

Three cases in `tests/clinic/test_payment_validation.py`
(`TestRefundingCashKeepsTheDrawerHonest`) cover the drawer and the ledger
agreeing after a refund, the same refund being refused a second time, and a
receipt and a refund coexisting for one payment. A fourth,
`test_a_cash_refund_goes_through_the_register` in the Phase 3c HTTP slice,
checks the refusal on the sales route and the whole thing end to end over
HTTP; `test_a_card_refund_still_goes_through_sales` checks that the refusal is
specific to cash and has not broken the route for everything else.

A unique constraint is a business rule, and this one was a business rule with
a typo in it. The lesson that generalises: a uniqueness rule over an entity
and a *reference* usually wants the kind of relationship in the key, because
the same two things can legitimately be related more than once in different
ways.

## ADR-041 — An organization may declare that it cannot separate duties

**Context.** Segregation of duties assumes somebody else is standing there.
The pairs in `SEGREGATION_OF_DUTIES` exist because the person who takes money
should not be the person who signs off that it was taken correctly, and in an
organization with a finance department that is both achievable and right.

This clinic is run by two or three members of one family. Until now the only
exemption was the **owner** (ADR-018), on the grounds that a single-owner MChJ
has nobody to delegate to — and that left two dead ends for everybody else,
both reachable on an ordinary day:

A cashier who counted the till and found it 5 000 short **could not close the
session at all.** Closing a session with a discrepancy needs
`cash.discrepancy.approve`, which the cashier role does not hold, and which
only `owner` and `director` do. If the owner was not in, the drawer stayed
open with the day's takings in it and no ledger entry for the shortage.

A cashier who took a payment in error **could not give it back.** The refund
route needs `sales.refund.approve`; the cashier holds `sales.refund.request`.

Neither refusal produces a second person. What it produces is a workaround —
and the workaround in a three-person clinic is that everybody signs in as the
owner, which does not weaken the audit trail so much as empty it.

**Decision.** The exemption becomes something the organization states about
itself: `organization.duties_cannot_be_separated`, declared by the owner,
with a mandatory reason, audited in both directions under
`organization.duties.declared` — an event in `REQUIRED_AUDIT_ACTIONS`,
because weakening an approval control is not something an audit
configuration gets to leave out. `duties_declared_at` records when it was
last looked at, since that is the first question a reviewer asks about a
relaxed control; *who* declared it lives in the audit row, which is the
authority, because a column can be updated and an audit row cannot.

`organization.duties.declare` is held by the `owner` role alone. It is the
one setting that decides whether a control applies to the person at the till
or to somebody standing behind them, and there is nobody above the owner to
ask.

**One function decides, everywhere.**
`one_person_may_do_and_approve(duties_cannot_be_separated, actor_is_owner)`
is the whole rule. Three places consult it: closing a session with a
discrepancy, refunding a cash payment, and granting a combination of roles to
one person. A fourth place that needs the same question later asks the same
function rather than growing its own version of the condition.

**What it does not do.** It grants nobody a permission — every operation
still requires one, and the declaration removes only the requirement that the
approver be a *different* person, which is the single condition a
three-person clinic cannot satisfy. It does not relax the read-only rule, so
an auditor stays an auditor. It does not admit unknown permissions. And it is
read from the organization inside the request, never taken from a request
body, so an endpoint cannot be talked into relaxing a control; a service that
is never told defaults to the strict rule, which is the safe direction to
fail in.

**Every act that relies on it says so.** A close performed by the person who
counted carries `self_approved: true` in its audit record, and so does a
refund given by whoever took the payment. The compensating control is a
readable record rather than a second signature that was never going to
exist — and `self_approved` is written on the row rather than left to be
worked out later by comparing two user ids that happen to match.

**No approval workflow was added, deliberately.** The obvious alternative —
reception *requests* a merge or a refund and somebody *approves* it — was
rejected for this clinic: there is no second authority to route the request
to, so the queue would fill with items only the owner could clear, which is
the dead end above with an extra step in front of it. If the clinic grows a
layer of management, the declaration comes off and the existing pairs apply
again, unchanged.

**`director` stops being offered.** It is the only role whose combination
with another trips a control, and in a clinic of three nobody is a middle
manager, so the assignment screen leaves it out while the declaration stands
(`ROLES_FOR_LARGER_ORGANIZATIONS`). It stays defined and stays grantable over
the API, for an organization that grows into it. Worth stating plainly: every
combination the clinic actually wants — receptionist with cashier with
dentist — was allowed all along, and a test asserts it, so this feature is
not a licence to assume every refusal needs a setting.

**Consequences.** `POST /organization/duty-separation` is the one way to set
it. Migration `0010` adds the column, the date, and a check constraint that a
declaration must carry a date. Eighteen cases in
`tests/security/test_duty_separation.py` cover the rule itself, the
declaration and its audit, both dead ends in both states, and the limits —
that an auditor is still read-only, that an unknown permission is still
refused, and that a till which balanced never needed any of this.

The honest summary: this does not make the clinic's books safer. It makes
them *true*, by removing the reason to share a login. A control nobody can
satisfy is not a control.

## ADR-042 — A duplicate patient is shown, compared and confirmed; never merged by matching

**Context.** The clinic has never had a patient number. The paper register
identifies people by family name and birth year, and the same person appears
on different days as unrelated rows, so two records that look alike are the
normal case rather than an anomaly. Two brothers with the same surname, born
the same year, are indistinguishable on the only key the clinic has ever
used — and a wrong merge joins two people's dental histories, which is not a
bookkeeping error.

**Decision.** Matching raises a question; a person answers it.

Registration is **never refused** on a suspicion: the receptionist has the
patient in front of them, and a wrong refusal costs more than a review. The
new record is created, the candidates come back with it, and a
`PENDING` review is queued for each pair.

The queue screen shows **both records side by side**, every identifying field,
including the ones that agree — a comparison showing only the differences
leaves the reader unable to tell a record with one typo from a record about
somebody else. Which record a merge would keep is stated *before* anybody
presses anything (`survivor_id`: the record already on file, not the one just
typed), rather than reported afterwards.

There are exactly **two outcomes, both decisions**: `DISTINCT` — these are
different people, recorded so the pair is never raised again — and `MERGED`.
Each requires a note. "Not a duplicate" is as much of a decision as a merge,
and the next receptionist to see these two records deserves to know somebody
already looked.

A merge is behind a **second, explicit confirmation** that names the record
being kept and the record being filed away, because it is the one action on
the screen that cannot be undone from the screen. It requires
`clinic.patient.manage`, and — per ADR-041 — there is no separate approval
step: in a clinic of three, routing the merge to a second authority means
routing it to nobody.

**The losing record is kept.** It is marked `merged_into_patient_id`,
deactivated, and left in place. Treatments were recorded against it, and an
audit trail that cannot say where they went is no audit trail. Clinical
records are not moved either: they stay attached to the encounter that
produced them, and the surviving record gathers them through the merge link.
`clinic.patient.merged` is in `REQUIRED_AUDIT_ACTIONS`.

**What counts as a difference is tested, not eyeballed.**
`lib/patientIdentity.ts` normalizes before comparing — trimmed, inner
whitespace collapsed, case-folded, telephone numbers reduced to digits — and
a blank on one side is reported as *missing*, not as a disagreement. Getting
that wrong in either direction is how a screen misleads: an invented
difference argues against a merge that was correct, and a hidden one argues
for a merge that was not. Eighteen frontend cases cover it, including the
cases from the register — two brothers, the same person typed twice with less
in the second row, and a household telephone number under two different
names. The screen displays the original spelling, never the normalized one.

**Consequences.** `GET /clinic/duplicate-reviews` returns the reviews with
both patient records attached, in one query for the reviews and one for every
patient they mention, rather than two round trips per row. Eighteen backend
cases in `tests/clinic/test_duplicate_review.py` assert the boundary this ADR
is about: that a matching name and birth year is reported and not refused,
that both records stay live and separate until somebody decides, that a
settled pair is not raised again, and that a merge keeps what it supersedes.

## ADR-043 — A data migration states what it seeds; it never reads the catalogue

**Context.** `permission` and the system roles' grants are reference data: the
application's own permission catalogue, in the database, so that a role grant
can be enforced by a foreign key rather than by a string somebody hopes is
spelled correctly. Migration `0001` seeds both by importing
`app.core.permissions.PERMISSIONS` and writing whatever it finds.

On a fresh database that is always correct, and that is exactly why it went
unnoticed. Every test run rebuilds the database from `base`, so `0001` seeds
today's catalogue and the two always agree. On a database migrated before a
permission was written, nothing delivers it — there is no later migration, and
nothing reconciles at startup.

Two permissions were therefore dead in every existing deployment.
`sales.debt.write_off` shipped with a route, a service, an audit event and an
owner-only grant in the role definitions, and no role in a real database held
it. `organization.duties.declare` made the whole duty-separation declaration
unreachable: the owner was refused their own setting with a 403, which is how
it was found — by opening the screen in a browser against a database that had
been migrated months earlier.

**Decision.** A data migration declares its contents literally. `0011` carries
an explicit `LATE_PERMISSIONS` and `LATE_GRANTS`, written out at that
revision, and imports nothing from the application. A migration that reads
today's code does something different depending on when it runs, which means
the schema a database ends up with depends on the order history happened in —
the one thing a migration chain exists to prevent.

It is additive and idempotent: `ON CONFLICT DO NOTHING` on both tables, so it
is safe on a fresh database where `0001` already seeded everything, and on one
that is behind. Nothing is removed. Withdrawing a permission has consequences
for whoever holds it and belongs in a migration of its own, written
deliberately, not as a side effect of reconciliation.

**The guard is a declared list, not a convention.**
`tests/unit`-style assertions could not catch this, because the test database
is always current. So `tests/security/test_permission_delivery.py` holds the
catalogue frozen: adding a permission fails the suite until the author
re-declares it, which is the moment to ask how it reaches a database already
in service. The same file checks the live database both ways — every catalogue
permission has a row, every row is in the catalogue, and every system role's
grants match its definition exactly, because an extra grant is authority
nobody wrote down and this project has shipped one of those before (the
`director` role holding both sides of a purchasing control).

It also checks `0011`'s frozen lists against the catalogue they claim to
deliver. A frozen list that has silently drifted is worse than not having one.

**Consequences.** `0001` is left as it is: rewriting an applied migration to
freeze its list would change what a fresh install seeds for no benefit, since
`0011` now reconciles anything `0001` missed and the declared list catches
the next addition. The lasting rule is the one in the title, and the cost of
breaking it was two features that worked on every machine except a real one.

## ADR-044 — The dashboard summarises only what the reader may already see

**Context.** The home screen showed the organization's registration details
and a list of module statuses. Both were accurate and neither was any use at
eight in the morning: the owner of a three-person clinic wants to know who is
coming in, what is still owed, how much went through the till, and whether
anything needs attention before it becomes a problem. The registration
details are looked at once a year, and the module statuses are a developer's
question.

Replacing that with figures raises two risks worth deciding about rather than
discovering.

**Decision — every figure is derived on the server, and borrowed rather than
restated.** `GET /clinic/today` returns the whole screen. The outstanding
debt comes from `ReceivablesService`, which is the one definition of what a
patient owes (ADR-026); the drawer's expected cash comes from
`CashSessionService`, which derives it from the movements. The service
computes nothing a report already computes, because the cheapest way to get
two different answers is to write the derivation twice, and the first time a
dashboard disagrees with the ageing report, nobody trusts either again. A
test asserts the two agree to the som.

It lives in `clinic` because `clinic` is the only module above both `sales`
and `cash` (ADR-030), so it can read a receivable and a drawer in one place
without either module learning about the other — and it reads them **through
their services**, never their models (ADR-036). The first version of this
read model ran its own aggregate queries over `Payment` and `CashSession`,
and the architecture test refused it, correctly: "today's takings" and "what
should be in the drawer" are definitions those modules own. They are now
`BillingService.takings_on`, `BillingService.total_unapplied` and
`CashSessionService.open_drawer`, which is also where a second caller will
look for them.

**Decision — a figure the reader may not see is absent, not zero.** Each
section is gated on the permission that guards the screen it summarises, and
a section the caller may not read is **not queried at all**: it returns
`null`, and the tile does not render. Zero would be worse than hiding it,
because `0 UZS owed` shown to a receptionist who may not read the total is a
lie in the shape of a fact.

The gate matters more than it looks. The clinic-wide debt is the same
aggregate the ageing report serves, and that report requires
`reporting.financial.read` — so the dashboard requires it too, even though a
receptionist may read an individual invoice. An aggregate is a capability of
its own, and a summary screen is not a way around the permission that guards
it. `drawer_readable` is carried separately so the interface can tell "no
session is open" apart from "not your business"; otherwise both are `null`
and the screen has to guess.

The endpoint itself carries no module gate, so everybody gets a dashboard:
the accountant, who may not read a patient, gets the financial half; the
dentist gets the clinical half and no figures. A page of nulls is a better
answer than a 403 on the home screen.

**Decision — no decorative controls.** A quick action appears only when the
person may perform it *and* the screen it opens exists. There is deliberately
no "record a treatment" button: the encounter and per-tooth screens are not
built, and a button that opens nothing is worse than no button, because
somebody plans their morning around it. What the dashboard does instead is
count the unfinished and unbilled work and say where it stands.

**Consequences.** The technical module list moved to Settings → System
status, generated from the backend registry with no status label hardcoded in
the frontend — there is deliberately no mapping a developer could edit to
make a module look finished.

The rules — which tile appears, with what emphasis, where it leads, and which
action is offered — live in `lib/dashboard.ts` as pure functions with 27
tests, separate from the component that renders them. That split caught its
first defect immediately: the till tile kept the heading "Till open" above a
dash when no session was open.

## ADR-045 — Part of a payment is given back as its own record, out of the advance

**Context.** The system could refund a payment, and only in full: every entry
the payment made was reversed and `Payment.status` became `REFUNDED`. That is
the right instrument for money taken in error, and it is not the refund this
clinic actually gives. The commonest one by far is the patient who hands over
800 000 for a bill of 600 000 and wants the 200 000 back. There was no way to
record that, which means it either did not happen at the desk or it happened
off the books — and the second is what really happens.

A partial refund cannot be made out of the full one. There is nothing to
reverse partially; the payment is still live; and a second partial refund has
to be able to follow the first.

**Decision — each refund is a record, and what is left is derived.**
`payment_refund` holds one row per handing back, with its own number, reason,
day and actor. What remains refundable is `amount − allocated − refunded`,
computed on demand. This is the same shape as an invoice's balance (ADR-026)
and for the same reason: a stored remainder is a second place for the truth to
live, and the two drift the first time a write fails halfway.

A refund is never edited or deleted. Money refunded in error is taken again as
a payment, so the record keeps both facts.

**Decision — only the unapplied part may be given back.** Money already
applied to an issued invoice is revenue recognised against work the clinic has
billed for. Handing it back without crediting the invoice would reopen the
invoice as a debt: the patient would be refunded *and* still owe for the same
treatment. Crediting an issued invoice is a credit note, the clinic has no
approved rule for one (`OPEN_QUESTIONS.md`), and inventing one quietly inside
a refund is how a ledger acquires a rule nobody agreed to. So the service
refuses, and the refusal names the instrument that is missing rather than
guessing one.

**Decision — the ledger entry is the advance posting, reversed.** The money
arrived as `advance_received_*`: debit cash, credit 6310, a liability the
clinic owes back. Giving it back discharges exactly that liability, so the
entry is that rule's own two accounts the other way round. No new account
mapping is invented, no new policy rule is required, and the feature therefore
works against a policy version that predates it — which matters, because the
active policy is the accountant's document and not ours to amend.

Each refund posts under its own `source_id`, not the payment's: a second
partial refund is a second business event, and `uq_posting_event_source` would
otherwise refuse it as a duplicate.

**Decision — the drawer's uniqueness rule becomes partial.** ADR-040 made a
cash refund a movement out of the till, protected by
`(organization_id, payment_id, kind)` — one receipt and one payout per
payment. That is right for a full refund and wrong for several partial ones.
The constraint becomes a *partial* unique index over whole-payment movements
(`payment_refund_id IS NULL`), and a second partial index guarantees one
movement per refund record. The protection ADR-040 was written for — the same
refund paid out of the till twice — is kept in both cases; what is dropped is
only the assumption that a payment can be refunded once.

**Consequences.** A payment that has been partly refunded can no longer be
refunded *in full*: reversing the whole entry on top of a refund already paid
would hand the money over twice. The refusal says so and points at the
remainder. Allocation subtracts refunds too — money given back cannot settle a
bill any more than it can be refunded again.

The day's takings are reported net of the day's refunds, because a tile
reading "taken today, cash" that ignored money handed back would not agree
with the drawer, and the drawer is the one figure a cashier can check by
counting.

`self_approved` is recorded on the refund as well as in the audit row, because
the refund list is what a reviewer actually reads. It is recorded, not
enforced: whether one person may both take money and give it back is decided
at the edge, where the duty-separation declaration is known (ADR-041).

## ADR-046 — Business dates come from the clinic's clock; instants stay UTC

**Context.** `date.today()` is the server's day. The server's day is the
container's day, which is UTC. The clinic is in Tashkent, five hours ahead.
Between seven in the evening and midnight those are different dates — and
that is exactly when a dental clinic is still seeing patients.

Everything a date decides was wrong for those five hours: a payment taken at
nine in the evening was received *yesterday*; the dashboard showed yesterday's
takings as today's; an appointment booked for this evening did not appear
under today; and a cash session opened in the evening carried a business date
whose drawer had already been counted and closed.

**Decision — one function, `app.core.clock.clinic_today()`, and no caller
uses `date.today()` for a business date.** The module also answers the current
instant and time of day in the clinic's zone. Timestamps are untouched:
`created_at`, `occurred_at` and every other instant stay UTC with a timezone,
which is the only sane way to store a moment. A **date** is not an instant. It
is a day in the place the work happened, and that place is Tashkent.

**Decision — an appointment's time of day is a local time, not an instant.**
The clinic books "Tuesday at three". Stored as a timestamp, that becomes a
different hour for anybody reading it from a different zone, including the
server. `Appointment.scheduled_on` is a `Date` and `scheduled_at` is a `Time`,
and the Python annotation now says so — it claimed `datetime | None` over a
`Time` column, which is the kind of lie that holds until somebody does
arithmetic with it.

**Consequences.** The zone is a constant, not a setting, because this system
runs one clinic. When it runs two in different zones it becomes a column on
`organization` — and every caller already goes through one function, so that
change is a signature and not a search.

The browser passes compute the clinic's day the same way (`Intl` with
`timeZone: 'Asia/Tashkent'`), because a verification run on a machine in
another zone would otherwise look for today's bookings under yesterday.

## ADR-047 — One dentist, one chair; and the roster is not the staff directory

**Context.** Reception books the day. Two things about that had to be decided
rather than discovered: what happens when the same hour is given to the same
dentist twice, and how a receptionist is shown the list of dentists.

**Decision — an overlap is refused by the server, under an advisory lock.**
Two people at two machines both read the day, both find three o'clock free,
and both book it: neither transaction can see the other's uncommitted row, and
there is no row to lock because the conflict is with one that does not exist
yet. So booking takes a transaction-scoped advisory lock on the dentist and
the day — the smallest key that makes the pair serial without serialising the
whole book — and the second booking then finds the first and is refused with a
409 that names the booking it clashed with. A booking with no hour clashes
with nothing: "Tuesday" is not a claim on a chair. A cancelled or finished
booking holds no chair either.

The browser warns about the clash before the request is sent, and the server
refuses it anyway. The warning is a courtesy; the refusal is the rule.

**Decision — the appointment form reads a roster, not the user list.** The
dentist picker first asked `GET /users`, which needs `identity.user.read`.
Reception does not hold it, so the only people who book anything were offered
an empty picker — found by driving the screen in a browser, and invisible to
every test that signed in as the owner. The fix is not to widen reception's
permissions: a receptionist has no business reading staff e-mail addresses,
roles and sign-in history.

`GET /clinic/dentists` answers an id and a name and nothing else, behind
`clinic.appointment.read` — the same permission as the day the form is drawn
beside, where those names already appear. Who holds the `dentist` role is
identity's question, so the clinic module asks
`IdentityService.active_members_with_role` rather than querying
`membership_role` itself (ADR-001's boundary rule). The owner is included,
because in a clinic of three the owner is usually also the dentist; somebody
whose membership or sign-in has been deactivated is not, because a booking
handed to somebody who cannot open the visit is a booking nobody works.

**Consequences.** The cashier reads the day and cannot change it —
`clinic.appointment.read` without `.manage`. That is deliberate: the person
taking the money is asked all morning who is still expected, and answering
that is not the same as owning the book.

An appointment remains the one part of the clinic that moves no money. No
status it can reach — confirmed, arrived, finished, cancelled, no-show —
posts anything, and a test asserts the ledger is untouched by a day's worth of
them. Attendance is not revenue; the clinic's own register shows several
attendances a week that produce none.

## ADR-048 — A course of treatment moves through states a clinician would name, and cannot be finished over unfinished work

**Status:** Accepted · 2026-11-03

**Context.** `treatment_plan` has existed since migration 0007 and
`POST /clinic/plans` since Phase 3c, but there was no way to read a plan back,
no way to move it, and no screen. The clinic's paper register shows prosthetic
work running across days as a matter of course — an impression one day, a
fitting a week later — so the table was right; what was missing was everything
around it.

**Decision — the transitions are data, not a chain of `if`s.**
`PLAN_TRANSITIONS` maps each state to the states that may follow it, on the
server and again (deliberately, narrowly) in the browser. The server is the
authority and refuses an illegal move with a 409 that **names what would have
been allowed**, which is only possible because the table exists to be read.
The browser's copy decides nothing; it only stops the screen *offering* a
button that is going to be refused. If the two ever disagree, the worst case
is an offered action that fails — never an accepted one that should not have
been.

A plan runs draft → proposed → agreed → in progress → finished, or is
abandoned from any live state. A proposal may go back to draft: a patient
asking for a change before agreeing is the ordinary case, and re-drafting is
honest where editing a proposal in place is not. Finished and abandoned are
final — somebody who returns gets a new plan, not a resurrected one.

**Decision — a plan cannot be finished while a tooth on it is still open.**
Refused with a 409 naming how many items are outstanding, rather than
silently cancelling them. What happens to an unfinished tooth is a clinical
decision belonging to the dentist, not a side effect of pressing a button on a
summary screen. The screen says so before the button is pressed, and disables
it, rather than explaining afterwards.

**Decision — a plan carries no money.** No price, no total, no quotation.
Billing still happens from completed items through the patient's account,
where the permission to raise a bill lives. A plan that priced itself would be
a second, informal invoice — and the one thing this system refuses to have is
two places where an amount is decided (ADR-026).

**Consequences.** Work is attached to a plan when it is recorded, and only
*open* plans are offered: attaching new work to a finished plan would reopen a
decision somebody already made. A plan's progress is derived from its items
every time it is shown, with cancelled and corrected items counted on neither
side — counting a cancelled tooth as done would show a plan as finished when
the work was abandoned, and counting it as outstanding would show one that can
never end.

## ADR-049 — Reading a clinical record is audited, because the document already said it was

**Status:** Accepted · 2026-11-03

**Context.** `SECURITY.md` has stated since Phase 1 that
`clinic.clinical.read` is marked sensitive "so such reads are audited". The
permission was enforced. The event `clinic.clinical.read` was defined. It was
listed in `REQUIRED_AUDIT_ACTIONS`. **Nothing ever wrote it.** Reading a
patient's treatment record left no trace of who had looked — which is exactly
the question a privacy complaint asks, and the one the document promised an
answer to.

This was found by auditing the module against the code rather than against the
documentation, which is the only way it could have been found: every test
passed, because no test asserted the row existed.

**Decision — a clinical read writes an audit row and commits it.** Listing a
patient's visits, their treatments, or opening a course of treatment each
record who looked, at whose record, and when. A `GET` committing is unusual
here and deliberate: the audit row is the only thing written, and it has to
survive the response.

**Consequences.** Three endpoints now write on a read. The cost is one insert
on screens that are opened a few dozen times a day in a clinic of three. The
alternative — a document describing a control the system does not have — is
worse than having neither.

The general lesson is recorded with the decision: a security control stated in
a document and never asserted by a test is a claim, not a control. Phase 7's
hardening should include a test that every action in `REQUIRED_AUDIT_ACTIONS`
is actually emitted by some code path, which would have caught this on the day
it was written.

## ADR-050 — An employee is not a user, and neither module answers the other's question

**Status:** Accepted · 2026-11-03

**Context.** Phase 5 needs employees: payroll has nobody to pay without them,
an employee document has nothing to attach to, and purchasing cannot record
who asked for something. The obvious shortcut is to treat the existing
`app_user` row as the employee — in a family clinic the same human is usually
both — and hang a salary and a job title off it.

**Decision — two records, one link, and the link is nullable.** `app_user` is
how somebody signs in. `employee` is somebody the clinic employs.
`employee.user_id` joins them where both exist, and is unique per
organization so one person cannot end up with two records and therefore two
payslips. It is nullable because a cleaner can be paid without ever signing
in, and a visiting accountant with a login may be no employee at all.

**Decision — roles stay on the membership.** The brief asks that one employee
be able to hold several roles; that already works, because roles hang off the
membership and `membership_role` has always been many-to-many. The employee
screen *shows* those roles, read from identity on every request, and stores
none of them. A copy would be a second answer to "what may this person do",
while the first answer is the one the API actually enforces against.

**Decision — the employees module never creates a login or grants a role.**
Hiring somebody and giving them authority over the books are two decisions
with two permissions. An endpoint that did both would let `hr.employee.manage`
mint an account with roles on it, which is a privilege-escalation path through
a module nobody would think to look at. Linking to an **existing** member is
allowed; creating the member is `identity.user.manage`, under Settings.

**Decision — the employees module stores no money.** No salary, no rate, no
accrual. Compensation is payroll's and reaches the ledger through the posting
policy like everything else. A wage column here would be the start of a second
payroll ledger.

**Decision — the rota is not the timesheet.** `work_schedule_day` says what a
normal Tuesday looks like; `attendance_day` says what happened. Payroll will
read the second and never the first. Two tables because they answer different
questions, and because a system that paid people from the rota would pay them
for days they did not work.

**Consequences.** Two invariants are held by the database, not merely by the
service: an employment is `ENDED` exactly when it has a leaving date
(`ck_employee_ended_has_date`), and hours exist only on a day that was worked
(`ck_attendance_day_hours_match_code`). Both are the kind of thing that, let
through once, becomes a payment nobody can explain. A third —
`uq_attendance_day_employee_date` — makes a second row for the same day
unrepresentable, which is the bug that pays somebody twice.

Ending an employment is its own operation with a mandatory reason and an
always-audited record, rather than one value in a general-purpose edit. It is
financially material and personally consequential, which is the combination
the audit trail exists for. The record is kept, never deleted: payslips and
attendance still point at it.

The module's five attendance codes are **its own**, and the screen says so.
The statutory timesheet uses twelve single-letter codes whose Uzbek wording is
unconfirmed (`OPEN_QUESTIONS.md` P1); mapping onto a form somebody signs needs
the real wording and is not guessed. This is why the module reports `partial`
rather than available.

## ADR-051 — A missing policy rule is its own error, not a missing record

**Status:** Accepted · 2026-11-03

**Context.** Posting anything resolves a rule from the accounting policy in
force. When the policy has no rule for the operation, `resolve_policy_rule`
raised `NotFound` — a 404, which the frontend renders as "The requested
record was not found."

A browser pass registering a supplier invoice met exactly that message. It is
wrong in three ways: nothing the caller asked for by id is missing; the user
has no way to act on it; and it reads as "that invoice does not exist", which
is alarming and false. The same message has been produced by the debt
write-off since Phase 3, where `OPEN_QUESTIONS` A7 deliberately leaves the
rule unmapped — so the system's most carefully designed "fail loudly" was
failing *quietly and misleadingly* instead.

**Decision — `PolicyRuleMissing`, a 409 with code `policy_rule_missing`,
whose message names the operation.** "The accounting policy in force has no
rule for `'supplier_invoice_materials'`, so this cannot be posted. An
accountant must add the rule to a new policy version first." The frontend
shows that message as sent, as it already does for a conflict.

409 rather than 422: the request is well formed and would succeed once the
policy says how to post it. 404 would be read as a missing document.

**Consequences.** Every operation that posts under the policy now fails in a
way somebody can act on — including the write-off that was built to fail.
This matters at deployment: a clinic whose policy was activated before a
module existed will meet this on the module's first use, and the message is
what tells them to add the rule rather than file a bug.

The general lesson is the same as ADR-049's: a control nobody has exercised
is a claim. `A7` was designed to fail loudly and nobody had looked at what
the failure said.

## ADR-052 — Stock is a ledger of movements, never a stored quantity

**Status:** Accepted · 2026-11-04

**Context.** The obvious shape for inventory is a `quantity_on_hand` column
updated on every receipt and consumption. It reads fast and it is wrong in
the way balances are always wrong: the column and the events that produced it
drift, and nothing says which is right.

The clinic counts its shelves. A stored total's drift therefore gets *found*
— usually weeks later, usually by somebody blaming the person who counted.

**Decision — there is no `quantity_on_hand` anywhere in this module.** What
is on a shelf is `SUM(quantity)` over `stock_movement`, computed when asked,
exactly as an account balance is the sum of its journal lines. Every movement
is signed, names the record that caused it, and is never updated or deleted.

A mistake is corrected by an approved count, which writes a further movement:
both the error and the correction stay on the record.

**Consequences.** A balance costs a grouped sum rather than a column read. At
the volume a three-person dental clinic generates — a few thousand movements
a year — that is indistinguishable, and the index on
`(organization_id, item_id, location_id)` is there for when it is not.

The test that matters is the one that deletes a movement and watches the
balance change. A stored column would have kept answering the old number.

## ADR-053 — Stock is counted long before it is costed

**Status:** Accepted · 2026-11-04

**Context.** `OPEN_QUESTIONS.md` I1 — which inventory valuation method the
clinic uses — has never been answered. The textbook response is to pick
weighted average, since it is the common one. That would be inventing a
statutory classification, which this project does not do.

But *quantities* need no such answer. Knowing there are two boxes of
composite left, and that a lot expires in three weeks, is the daily value of
an inventory module and none of it depends on what the stock is worth.

**Decision — the organization declares `inventory_valuation_method`, and it
is `UNDECLARED` for everybody until an accountant says otherwise.** This is
deliberately the same shape as the revenue tax treatment beside it (ADR-022).

While it is undeclared: quantities are tracked from the first day, low stock
is flagged, expiry is warned about, and `GET /inventory/valuation` returns
**every quantity with every cost null** rather than refusing the page or
guessing a number. A consumption records a quantity and **posts nothing** —
what the material cost is already in the books, debited to 1010 when the
supplier invoice was registered.

`WEIGHTED_AVERAGE`, once declared, is derived from the supplier invoices
already on file and needs no new storage. `FIFO` says it is not implemented,
because it would need cost layers this system does not keep, and saying so is
better than averaging and calling it FIFO.

**Consequences.** The clinic gets the useful half of inventory immediately
and the accounting half when somebody with authority answers one question.
Nothing had to be built twice to achieve that.

The module reports `partial` for exactly this reason, and the screen says so
in the valuation panel rather than showing an empty table.

## ADR-054 — Stock arrives through one door, and it is the delivery

**Status:** Accepted · 2026-11-04

**Context.** Something has to create the movement that puts stock on a shelf.
The easy answer is an "add stock" screen. The consequence of that answer is
stock whose origin nobody can establish, and a quantity on a shelf that no
delivery note explains.

**Decision — there is no endpoint that creates a `RECEIPT` movement.**
Recording a goods receipt in purchasing is what puts stock on a shelf: the
receipt line names the material, and the accepted quantity becomes a movement
in the same transaction. The API slice test asserts the *absence* of
`POST /inventory/receipts`, because an absence nobody checks is an absence
that gets filled in later by somebody being helpful.

Only what was accepted is shelved. Goods turned away at the door never
entered the clinic, and recording them as stock and writing them off again
would invent two movements for something that never happened.

**Consequences.** Stock cannot be created without a supplier and a delivery
date, which is the point. The clinic that buys gypsum from a market stall
still records the receipt — a receipt without an order was already allowed —
so nothing is pushed off the books to avoid paperwork.

The authorised way to disagree with the ledger is a counted difference
approved by somebody who did not count it, not a correcting entry typed by
whoever noticed.

## ADR-055 — Valuation is composed in purchasing, to keep the graph acyclic

**Status:** Accepted · 2026-11-04

**Context.** Valuing stock needs two things owned by two modules: **how
much** is on the shelf, which is inventory's movement ledger, and **what it
cost**, which is on the supplier invoices purchasing holds. Purchasing
already calls inventory, to put a delivery on a shelf. Putting valuation
inside inventory would make inventory call purchasing, and a cycle is how a
modular monolith stops being modular.

**Decision — `purchasing/services/stock_valuation.py`.** The module that
already composes the other keeps the composition, the same shape as the cash
module composing purchasing to pay a supplier out of the till (ADR-042). The
API layer, which may import anything, serves it at `/inventory/valuation`
because that is where somebody looks for it.

Inventory itself has no concept of money: no cost column, no amount on a
movement. What materials cost is in the ledger once, in account 1010.

**Consequences.** The architecture guard keeps holding without an exception
being added to it, which is the test of whether a layering rule is real.

A weighted average is computed from registered bills only — a draft is what
somebody typed, and a voided bill is one the clinic says never happened.

## ADR-056 — A quantity is not money, and does not share its arithmetic

**Status:** Accepted · 2026-11-04

**Context.** The frontend has careful money arithmetic in `money.ts`: exact,
in minor units, two decimal places, because that is the scale of a currency.
The obvious move is to reuse it for stock quantities.

It is wrong. A material is bought by the box and used by the millilitre, and
`stock_movement.quantity` is `NUMERIC(20, 4)`. Running a quantity through the
money helpers silently truncates 0.0005 of an anaesthetic to nothing.

**Decision — `inventory.ts` carries its own four-decimal arithmetic**, in
`bigint`, with the same discipline and a different scale, and a
`displayQuantity` that drops trailing zeros for the reader.

**Consequences.** Two small arithmetic modules instead of one, which is the
right number: they model two different things that happen to both be
decimals. The unit test that pins this asserts `0.0005 + 0.0005 = 0.001`,
which the money helpers would have reported as zero.

The display half of this was a real defect, found by driving the screen
rather than by any test: the shelf read **"10.0000 box"** where the clinic
counts ten boxes.

## ADR-057 — A statement is derived from the trial balance, never stored

**Status:** Accepted · 2026-11-04

**Context.** Profit and loss and the balance sheet could each query the
ledger their own way. If they did, three reports would answer the same
question by three routes, and the day they disagreed nobody would know which
one to believe.

**Decision — both statements are built from `ReportService.trial_balance()`,
the same call the trial balance screen uses.** A statement groups its rows by
`AccountElement` and sums them; it issues no ledger query of its own. The
trial balance already excludes drafts and already proves it balances, so both
properties come along for free.

Two consequences follow, and both are asserted by tests. A statement cannot
disagree with the trial balance, because it *is* the trial balance rearranged.
And the balance sheet's result for the year equals the profit and loss's net
result for the same window, because the two numbers come from the same rows.

`TrialBalanceRow` gained `needs_confirmation` for this: a statement has to
say whether a classification is confirmed, and the row is where that fact
belongs.

**Consequences.** Reporting owns no SQL against the ledger. The one query it
genuinely needed that accounting did not already expose — every posted line
in a window, flat, so the cash statement can see both sides of an entry — was
added to accounting as `entry_lines()` rather than written in reporting
against tables it does not own. The architecture guard caught the first
attempt, which imported `accounting.models` directly.

## ADR-058 — Nothing is classified silently, and a statement says whether it balanced

**Status:** Accepted · 2026-11-04

**Context.** The seeded chart of accounts is marked `needs_confirmation`
while `OPEN_QUESTIONS.md` A1 is unanswered, and an account may carry no
`element` at all. A statement meeting such an account has three options:
drop it, guess where it goes, or show it.

Dropping it makes the statement balance by losing money. Guessing invents a
statutory classification, which is the one thing this project does not do.

**Decision — show it, in its own section, and count it.** Every statement
carries an `unclassified` section and an `accounts_needing_confirmation`
count, and the screen marks each such line. The balance sheet additionally
returns `balances` and `difference`, and when it does not balance the screen
says so in its loudest available form rather than rounding the difference
away.

**Consequences.** A statement can look untidy, and that is the correct
outcome: the untidiness is real and it points at a question somebody needs
to answer. A tidy statement built by absorbing an unclassified account into
revenue would be a lie that nobody could see.

The same principle gives the cash statement its `reconciles` flag: opening
plus movement must equal closing, and if it does not, the report says so.

## ADR-059 — Cash is what the clinic configured, and its movements are not classified

**Status:** Accepted · 2026-11-04

**Context.** A cash flow statement needs to know which accounts hold cash.
The tempting answer is the statutory account range — anything beginning with
5 — which would be inventing a classification while A1 is open.

The second question is how to group the movements. The textbook answer is
operating, investing and financing; which method a filing requires here is
`OPEN_QUESTIONS.md` A3, unanswered.

**Decision — cash is the set of ledger accounts the clinic's own tills and
bank accounts are attached to.** A cash register names its account; a bank
account names its account. That is configuration the clinic made, not a
range this project guessed.

**Movements are attributed to the other side of their own journal entry**, and
grouped by that account, with no three-way split. Most entries have exactly
two lines, so the attribution is exact; where an entry has several non-cash
lines the cash is apportioned in proportion to their amounts — arithmetic
over that entry's own figures, never a judgement about what a payment was
for. A transfer between two cash accounts is excluded: it changes no total
and belongs nowhere on a statement of what the money was for.

**Consequences.** The statement answers "what came in, what went out, what
for" exactly, and declines to answer "operating or investing" at all, saying
so on the screen. When A3 is answered, the classification is a grouping over
figures that are already correct.

## ADR-060 — A spreadsheet from the server, a PDF from the browser

**Status:** Accepted · 2026-11-04

**Context.** The reports have to leave the screen: an accountant wants the
file. Excel is straightforward — `openpyxl` is already a dependency, used by
the bank statement importer. PDF is not: it needs a layout engine, and the
candidates are a large native dependency or a headless browser, both heavier
than the problem a three-person clinic has.

**Decision — export to `.xlsx` on the server, and print to PDF in the
browser over a print stylesheet.** The spreadsheet writers take the report
object the API already returned and lay it out; they query nothing, so the
file holds the figures the screen was showing. The print stylesheet drops
the shell, the tabs and every control, forces white paper and black ink even
in dark mode, and keeps table rows from splitting across sheets.

**Consequences.** No PDF dependency, and "Save as PDF" gives a clean page
with the clinic's name, the period, and the same footnotes the screen
carries. The limitation is real and stated: the layout is the browser's, so
a filing form with a prescribed layout could not be produced this way — but
those are blocked on A2 anyway, and will need their own answer when they are
not.

## ADR-061 — The reports screen renders the backend's catalogue of what it can report

**Status:** Accepted · 2026-11-04

**Context.** The Reports page used to be a "module not developed yet"
placeholder. That message was wrong in both directions: the trial balance and
the account statement had worked since Phase 2 and were sitting behind it,
and the statutory forms that genuinely cannot be produced were not mentioned
at all, so nobody learned that they wait on an accountant's answer.

A hand-written list in the frontend would fix today's version of the problem
and recreate it the next time a report is added.

**Decision — `GET /reporting/catalogue` answers what this build can produce**,
including the blocked reports, each naming the open question that blocks it:
A2 for the statutory statement forms, A3 for the statutory cash flow, A5 for
a VAT return. The screen renders that answer, and lists the blocked ones at
the foot of the page.

**Consequences.** A report cannot be offered that does not exist, and a
report that works cannot be hidden behind a placeholder. The module registry
and this catalogue now say the same thing because both are generated from
the same facts, and the browser pass fails if the page ever shows "not
developed" again.

This is the general lesson of ADR-049 and ADR-051 once more: the system
should state what it can and cannot do, and the statement should be generated
rather than written by hand, because a hand-written one goes stale silently.
