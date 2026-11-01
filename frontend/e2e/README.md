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

Between them they have found thirteen defects, two of them in code that had
been passing its unit tests since Phase 2.

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

`policy.cjs` drives the policy editor as the owner — the only role that may
change it — building a version, adding and editing rules, putting it into
force, discarding a mistaken draft, and checking that an accountant sees the
same screen with no control that changes it. A step declares the HTTP
refusals it means to provoke, so the application correctly refusing something
is not reported as a fault.

All three are re-runnable against the same tenant: `banking.cjs` creates its
own bank account and seeds its own card payment, `policy.cjs` takes its
effective date from whatever is in force and clears any draft left behind, and
`clinic.cjs` creates its own staff, service and patients, then banks what it
took and closes the drawer it opened — a cash account may only hold what the
ledger says it holds, so a pass that left money in the till would be refused
the next time it tried to open one.

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
```

`clinic.cjs` adds the two accounts it needs — a receptionist and a cashier —
the first time it runs, and signs in as them afterwards.

Each prints `0 problem(s)` when every screen is sound, and otherwise lists
each problem with the step that produced it.

Environment variables: `E2E_WEB`, `E2E_API`, `E2E_TENANT`, `E2E_SHOTS`.

## What this is not

It is not a CI suite. Wiring a Playwright run into CI — with the stack brought
up as a service and browsers cached — is Phase 7 work, and is listed as a gap
in `docs/TESTING.md`. These scripts are the things that would be promoted.
