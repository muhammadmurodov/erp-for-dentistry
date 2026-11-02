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
