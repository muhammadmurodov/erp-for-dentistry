# Browser verification

Every phase of this project has been driven end to end in a real Chrome and
inspected screen by screen, because each pass has found defects that the type
checker, the linter and the backend tests all missed — a stale route after a
change of user, a `<select>` that widened the page, a header that pushed the
layout sideways on a phone.

Phases 1 to 3b were driven by hand. The scripts here are the passes kept so
they can be re-run after a change instead of re-done:

| Script | Drives | As |
| --- | --- | --- |
| `banking.cjs` | statement import, matching, bank accounts | the accountant |
| `policy.cjs` | accounting policy versions and rules | the owner |
| `clinic.cjs` | patients, the account, taking a payment | reception, then the cashier |
| `small-clinic.cjs` | the duty-separation declaration and the duplicate-patient queue | the owner, the cashier, then reception |
| `patient-to-payment.cjs` | the whole journey: register, examine, record teeth, finish, bill, pay | reception, the dentist, then the cashier |
| `refunds.cjs` | handing money back: the refundable figure, two partial refunds, the drawer and the ledger | the cashier |
| `appointments.cjs` | the day: booking, the clash, arrival, moving, cancelling | reception, then the cashier |
| `plans.cjs` | a course of treatment across visits, and correcting a record | the owner, then the cashier |
| `employees.cjs` | hiring, the rota, the monthly timesheet, ending an employment | the owner, then the cashier |
| `purchasing.cjs` | order, deliver, invoice, pay — and what each one posts | the owner, then reception |
| `inventory.cjs` | the shelf: a delivery arriving, a refusal to go short, a count somebody else approves | the owner, then the cashier |
| `reports.cjs` | every statement against the ledger it came from, the drill-down, the export, and what cannot be produced yet | the owner, then the cashier |

Between them they have found eighteen defects, two of them in code that had
been passing its unit tests since Phase 2, and two that no test run could have
found because every suite rebuilds the database from `base`.

## What they check

`banking.cjs` drives the three banking screens as the accountant who would
really use them — create a bank account, inspect the column mapping, import a statement,
re-import it, match a settlement with its commission, set a line aside and
bring it back — and fails on any of:

- a console error, a page error, a failed request or a 5xx;
- a page wider than the viewport, at desktop and phone widths, in all three
  languages (the mechanical form of the `<select>` defect found in Phase 2);
- the full bank account number appearing in the rendered page;
- an import that does not report the counts it should, or a re-import that
  does not report every line as already held;
- the auditor being offered a control their role does not carry.

`clinic.cjs` drives the clinic as the two people who share the work, because
that division is what the permissions encode: reception registers a patient
and may not touch the money, the cashier takes the payment and may not
register anybody. Between them it registers a patient, provokes the duplicate
warning with a second one of the same name and birth year, records and bills
the treatment over the API — the clinical screens do not exist yet — and then
settles the bill with part cash and part card in one submission. It fails on
any of:

- the form offering to take more than is owed, or reporting a remaining
  balance of nothing while it holds more than is owed;
- the remaining balance being editable, or disagreeing with the bill;
- cash being available with no drawer open, or unavailable with one;
- reception being offered a payment control, or an amount field it may type
  into on a form it cannot submit;
- the cashier being offered a registration control;
- the accountant, who may not read a patient, being offered the clinic at all;
- the ledger disagreeing afterwards: issuing recognises the revenue once,
  taking the money moves cash and clears the receivable without touching
  revenue again.

`small-clinic.cjs` drives the two things built for a clinic of three
(ADR-041, ADR-042), and is mostly about limits, because both features relax
something. It declares duty separation as the owner and checks that a cashier
is neither shown the card nor allowed the endpoint; that the reason is
mandatory on the screen and at the API; that the declaration lands in the
audit trail with the reason that was typed; that `director` stops being
offered while every role the clinic uses still is; that a cashier may then
close their own short till and the close is recorded as `self_approved`, while
the same close is refused with the declaration withdrawn; and that the
shortage still reaches account 9430 either way — relaxing who approves must
not change what the books say. Then it registers patients as reception and
works the duplicate queue. It fails on any of:

- the declaration being offered to, or accepted from, anybody but the owner;
- a declaration taken without a reason, or missing from the audit trail;
- the declaration granting any permission at all — a receptionist who can
  suddenly read the chart of accounts, the audit trail or the cash registers,
  or a cashier who can register a patient;
- two patients merging without a person confirming it twice, or the
  confirmation failing to name which record is kept and which is filed away;
- a brother — same surname and birth year, different given name — being
  reported as a possible duplicate;
- a superseded record being deleted rather than kept, marked and still
  readable by id;
- a name query returning patients matched on the digits of their telephone
  number;
- the queue being offered to somebody who may not settle one.

It leaves the tenant as it found it, including the declaration.

`refunds.cjs` drives the refund the clinic actually gives: not a reversal,
but a patient who handed over more cash than the bill and wants the
difference. It takes 800 000 for a 600 000 bill, reads what the dialog says
is refundable, tries to take more than there is, hands back part of it twice,
and then checks three things no page can tell you on its own. It fails on any
of:

- the dialog offering to refund money that is applied to a bill;
- the till's expected cash not falling by exactly what went out;
- the ledger disagreeing with the drawer afterwards;
- the bill the patient really did pay no longer reading as settled;
- a cash refund being offered with no drawer open, or offered without saying
  where to go to give one.

`appointments.cjs` drives the day as reception works it. What it is for is
the two things no unit test reaches: that a time typed at the desk comes back
as the same time, in the clinic's own hours, and that a double booking is
refused by the server and explained on the screen rather than discovered
later by two patients in one chair. It fails on any of:

- the screen opening on a day that is not the clinic's day;
- a clash that is not warned about before it is sent, or not refused when it
  is;
- a booking whose hour, reason or dentist is not what was typed;
- a patient who has arrived still being offered a no-show;
- a cancellation taken without a reason, or one that loses the reason;
- the cashier being offered a control over a book they may only read, or
  being refused the day they are asked about all morning.

It finds its own free hour rather than assuming one, and cancels the bookings
it made when it finishes — so a second run finds its hour where it expects to
and the clash it provokes is its own.

`plans.cjs` drives a course of treatment — the prosthetic work the clinic's
register shows running across days as a matter of course. What it is for is
the two rules that have to reach a person rather than only a 409: a plan moves
forward through states a clinician would recognise, and it cannot be declared
finished while a tooth on it is still open. It also drives the correction
screen, which until now had only an API endpoint behind it. It fails on any
of:

- a draft plan offering "Finished", or a finished plan still offering a move;
- a plan in progress offering to finish with work outstanding, or doing so
  without saying why not;
- the progress count disagreeing with the work on the plan;
- recording work not offering the open course of treatment to attach it to;
- a correction accepted with no reason;
- the superseded treatment disappearing, being rewritten in place, or losing
  the pointer to its replacement;
- the cashier being shown a finding, a treatment record or a course of
  treatment — or losing the patient account they do need;
- the patient page overflowing a phone, or showing English in Uzbek.

`employees.cjs` drives the employees module around the boundary it is built
on: **an employee is not a user.** The screen shows the login and the roles it
holds and must never become a second way to create one or grant one. It hires
somebody, sets their ordinary week, fills in the timesheet, and ends the
employment it created. It fails on any of:

- the employee screen offering to invite, create a user, or set a password;
- an employee with no login being shown roles they cannot hold;
- a saved rota coming back as something other than what was typed;
- a sick day offering an hours field, or a worked day not requiring one;
- a cell before somebody started, or after they left, being clickable;
- the timesheet opening on a month that is not the clinic's;
- the legend failing to say that these are not the statutory codes;
- an employment ended without a reason, or an ended one still offering to be
  suspended, reinstated or ended again;
- the cashier being offered the module in the navigation, shown a staff
  record, or given no reason when they ask for the page directly;
- the people list overflowing a phone, or showing English in Uzbek.

`purchasing.cjs` drives the four states the module keeps apart — ordered,
received, invoiced, paid — and checks the ledger between each of them,
because the thing most likely to be wrong is *when* something posts. It
reads the trial balance before and after approving an order and after
recording a delivery, and fails if either moved the payable. It fails on any
of:

- approving an order, or recording a delivery, moving account 6010;
- a drafted invoice moving it either;
- registering an invoice moving it by anything other than the invoice total,
  or leaving materials account 1010 undebited;
- the delivery still being offered for invoicing afterwards — the one
  guarantee the whole four-record shape exists for;
- the running total in the order form disagreeing with its lines;
- choosing a delivery not filling the invoice line from it;
- a cash payment offering neither a till nor a reason why not, or being
  submittable without one;
- reception being offered the module, shown a supplier, or given no reason;
- the screens overflowing a phone, or showing English in Uzbek.

`inventory.cjs` drives the store cupboard, and exists mainly to check three
things that no unit test can show reaching a person: that stock arrives
through the delivery rather than by hand, that a shelf cannot go short, and
that counting and approving are two people. It reads the trial balance
around both the delivery and the consumption, because the central claim of
the module is that moving a quantity posts nothing. It fails on any of:

- the stock screen offering any way to create stock by hand;
- a delivery not reaching the shelf, or reaching it while moving 1010, 6010
  or 5010;
- a material used on a patient moving any account at all;
- taking more off a shelf than is on it being allowed, or the refusal not
  saying what is actually there;
- a material with a reorder level and no stock not being reported as low, or
  still being reported after a delivery;
- the count sheet not showing the difference as it is typed, or not saying
  that saving moves nothing;
- saving a count changing the shelf before anybody approved it;
- the approve button being offered to whoever counted, or the server
  allowing that approval;
- the valuation panel showing a number instead of saying that no method has
  been chosen;
- the cashier reaching the module or being given no reason;
- the screens overflowing a phone, or showing English in Uzbek.

It creates its own material with a unique code, writes off what it bought and
deactivates the material at the end, so it can be run twice in a row — and
is.

`reports.cjs` exists to check the claim the reporting module rests on: that a
report cannot disagree with the ledger it came from. It does not check that
numbers appear. It registers a patient, bills them a known amount, and then
checks that each statement moved by exactly that much, reading the trial
balance through the API as a second opinion. It fails on any of:

- issuing the bill moving revenue by anything other than the amount billed;
- the profit and loss not saying it is a management report, or not marking
  the accounts whose classification is unconfirmed (A1);
- the comparative columns not appearing when the comparison is asked for;
- a report line that cannot be clicked, or a drill-down that shows no
  entries;
- a balance sheet whose assets do not equal its claims, or one that balances
  while showing the unbalanced warning;
- the ageing report disagreeing with the sales module's own figure for the
  same day;
- the cash statement not reconciling, or not saying its movements are
  unclassified (A3);
- the blocked statutory forms not being listed with A2, A3 and A5 — or the
  old "not developed" message appearing again;
- the Excel download not being a real workbook;
- the cashier reaching any report, or being given no reason;
- the page overflowing a phone, or showing English in Uzbek.

It runs against the **demo** tenant rather than `verify-3d`, and the reason
is itself a finding: it has to supersede the accounting policy, because a
tenant whose policy was activated before purchasing existed has no rule for a
supplier invoice. `verify-3d` cannot be superseded from today — repeated
`policy.cjs` runs have pushed its newest version's effective date years into
the future, and a new version must start after the one it replaces. That
refusal is correct; it just makes that tenant the wrong place to do this.

`policy.cjs` drives the policy editor as the owner — the only role that may
change it — building a version, adding and editing rules, putting it into
force, discarding a mistaken draft, and checking that an accountant sees the
same screen with no control that changes it. A step declares the HTTP
refusals it means to provoke, so the application correctly refusing something
is not reported as a fault.

All of them are re-runnable against the same tenant: `banking.cjs` creates its
own bank account and seeds its own card payment, `policy.cjs` takes its
effective date from whatever is in force and clears any draft left behind, and
`clinic.cjs` creates its own staff, service and patients, then banks what it
took and closes the drawer it opened — a cash account may only hold what the
ledger says it holds, so a pass that left money in the till would be refused
the next time it tried to open one. `small-clinic.cjs` does not assume what
the drawer holds either: it asks for a float of nothing and reads the
refusal, which is how it learns what the previous session counted without
reimplementing the rule.

Screenshots of every step land in `e2e/shots/`, which is not committed.

## Running it

It needs the stack running, Chrome installed, and a tenant to work in. It
never destroys data: each run creates its own bank account and seeds its own
card payment, so it can be run repeatedly against the same tenant.

```bash
# 1. Playwright, which is not a project dependency: the script drives the
#    Chrome already on the machine, so no browser download is needed.
npm install --no-save playwright

# 2. The stack.
make dev-backend   # in one terminal
make dev-frontend  # in another

# 3. A tenant with a chart, a fiscal year, an active policy and a customer.
#    `make seed` creates the demo organization; the accounting setup is the
#    same handful of calls the Phase 2 and 3 passes made.
cd backend && .venv/bin/python -m app.cli seed-demo

# 4. The passes themselves.
node frontend/e2e/banking.cjs
node frontend/e2e/policy.cjs
node frontend/e2e/clinic.cjs
node frontend/e2e/small-clinic.cjs
node frontend/e2e/patient-to-payment.cjs
node frontend/e2e/refunds.cjs
node frontend/e2e/appointments.cjs
node frontend/e2e/plans.cjs
node frontend/e2e/employees.cjs
node frontend/e2e/purchasing.cjs
node frontend/e2e/inventory.cjs
node frontend/e2e/reports.cjs
```

`/auth/login` is rate-limited to ten a minute, which is a control and not an
obstacle. Running several passes back to back can spend it: if one reports
being signed out or an account it created a moment ago as missing, wait a
minute and run it again.

The passes add the accounts they need — a receptionist, a cashier and, for
`appointments.cjs`, a dentist — the first time they run, and sign in as them
afterwards. `appointments.cjs` reads the directory to decide what is already
there rather than probing with a sign-in, which is the cheaper question and
the one that cannot be confused with a rate limit.

Each prints `0 problem(s)` when every screen is sound, and otherwise lists
each problem with the step that produced it.

Environment variables: `E2E_WEB`, `E2E_API`, `E2E_TENANT`, `E2E_SHOTS`.

## What this is not

It is not a CI suite. Wiring a Playwright run into CI — with the stack brought
up as a service and browsers cached — is Phase 7 work, and is listed as a gap
in `docs/TESTING.md`. These scripts are the things that would be promoted.
