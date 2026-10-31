# Security

## Authentication

**Passwords** are hashed with Argon2id at the OWASP second-choice profile
(19 MiB memory, 2 iterations, 1 lane). Hashes are rehashed transparently on
sign-in when the parameters change. Minimum length 12, configurable; a short
documented list of common passwords is refused outright — the real defence is
length plus rate limiting, not a dictionary.

**Access tokens** are short-lived signed JWTs (15 minutes by default) carrying
the user, the selected membership, and the resolved permission set.

Embedding permissions avoids a role-table query on every request, at the cost
that a revoked role stays effective until the token expires. The trade is
deliberate and bounded: the TTL is short, and anything that must take effect
immediately — deactivating a user, changing a password — also revokes the
refresh chain, so the worst case is one access-token lifetime.

**Refresh tokens** are opaque 48-byte random strings. Only a SHA-256 digest is
stored, so a database disclosure yields no usable sessions.

### Refresh rotation and replay detection

Every refresh consumes the presented token and issues a new one. Presenting an
already-rotated token revokes **every** session for that user, because that is
the signature of a stolen token being replayed: the legitimate client has
already rotated, so a second use of the old value came from somewhere else.

The frontend coordinates refreshes through a single shared promise. Without
that, several queries failing with 401 at once would each start a refresh, and
all but the first would present an already-rotated token — triggering the
defence against the user's own client.

### Enumeration resistance

A sign-in failure returns one message and one status regardless of cause. For
an unknown email a dummy Argon2 verification still runs, so the response time
does not distinguish a registered address from an unregistered one. Which
check actually failed is recorded in the audit trail, not returned to the
caller.

Requesting an organization the user is not a member of returns 403 **without
confirming the organization exists**, and the response body does not echo the
id.

### Lockout

After 8 consecutive failures (configurable) the account locks for 15 minutes.
A correct password is refused while the lock holds. The counter resets on
success.

The failed-attempt counter is committed even though the request fails — see
`DATABASE_DESIGN.md`. Without that, the error handler's rollback would discard
the increment and lockout would never trigger. This was a real bug caught by a
test.

### Rate limiting

Per-minute fixed windows. Sign-in is keyed on **both** client address and
submitted email, so one attacker cannot lock out an account by exhausting its
budget from elsewhere, and one address cannot spray many accounts.

Redis backs this wherever more than one worker runs; a per-process counter is
not a limit. The in-memory implementation says so rather than pretending to be
distributed. If Redis becomes unreachable the limiter logs a warning and
fails open — a rate-limiter outage must not take the API down — which is a
deliberate availability-over-strictness choice.

## Authorization

Authority attaches to a **membership**, the pair (user, organization), never
to a user. A cross-organization privilege is not expressible.

67 permissions named `<module>.<object>.<action>`, nine system roles, defined
once in `app/core/permissions.py`. Migrations seed the database from that
registry, so a grant is checked by a foreign key rather than a string.

`require_permissions(...)` is the only authorization gate in the HTTP layer and
every write endpoint declares one. Being a superuser is **not** a substitute:
platform administration and organization authority are separate, so a
superuser without a membership cannot post to a tenant's ledger.

### Segregation of duties

Five pairs that one person must not hold together:

| Must not combine | Why |
| --- | --- |
| request a refund + approve it | Self-approved refunds |
| raise a purchase order + approve it | Self-approved spending |
| submit an expense + approve it | Self-approved expenses |
| calculate payroll + approve it | Unreviewed payroll |
| operate a cash session + approve its discrepancy | Self-cleared cash shortfall |

Checked when a role is defined **and** when roles are granted, against the
*union* of their permissions — because a conflict can arise from combining two
roles that are each individually valid. Defining the `director` role surfaced
exactly that: it had been given both purchase-order permissions, and the check
failed the build until the role was corrected.

The owner role is exempt. A single-owner MChJ has nobody to delegate approval
to; the audit trail is what makes those actions reviewable. The exemption is
explicit in code, tested, and applies to no other role.

### Read-only roles

`auditor` may hold no write permission. A test enumerates every permission
whose name implies a write and asserts the role holds none.

### The frontend is not the boundary

It mirrors backend permissions to avoid offering actions that would be
refused. The authorization tests call the API directly, bypassing the UI, and
include a forged unsigned token carrying `organization.manage` — rejected at
the signature, before permissions are even consulted.

## Tenant isolation

`RequestContext.organization_id` comes from the signed token and from nowhere
else. **No endpoint accepts an organization id in a path, query or body.**
There is no identifier to substitute, which is stronger than validating one.

Two enforcement layers, described in `DATABASE_DESIGN.md`: query scoping, and
composite foreign keys that make a cross-tenant row unrepresentable.

Eleven tests cover this, including raw-SQL attempts to insert a cross-tenant
grant, and a check that a foreign record's id behaves identically to a
nonexistent one.

## Audit trail

Append-only, enforced by a database trigger: `UPDATE` and `DELETE` on
`audit_log` are rejected whatever privileges the caller holds. A test asserts
the trigger is installed, so a later migration cannot quietly remove it.

Recorded: who (id and a durable text label), which organization, the action,
the target, the outcome, the timestamp, the request id, the client address,
the reason where one is required, and the changed fields before and after.

### What is never recorded

`app/core/logging.py` defines a key list — passwords, tokens, hashes, card
numbers, CVVs, and clinical fields such as diagnosis and anamnesis — that is
stripped from both logs and audit values at the point of writing. So a
careless `log.info("...", password=...)` cannot leak a credential, and the
protection does not depend on every call site remembering.

Tests assert that a distinctive password used in a sign-in attempt appears
nowhere in the resulting audit rows.

Monetary values in audit records are stored as exact text, never as JSON
numbers, so a `Decimal` cannot become a float on the way through.

## Transport and headers

HTTPS is assumed to terminate at a reverse proxy. The API sets
`Strict-Transport-Security` when it sees an HTTPS request, plus
`X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`,
`Referrer-Policy: no-referrer`, `Cross-Origin-Opener-Policy: same-origin`,
a `default-src 'none'` CSP (the API serves JSON only), and
`Cache-Control: no-store` on every response — financial data should not sit in
an intermediate cache.

Headers are applied by the outermost middleware, so error responses carry them
too.

CORS allows only the configured origins. The default is the local dev
frontend, and the value is environment-driven.

`X-Forwarded-For` is trusted only because the service is expected to run
behind a proxy that sets it. It is used for audit context and rate-limit keys
and **never** for authorization.

## Secrets

Read from the environment only. `.env` is gitignored and a CI job fails if it
becomes tracked. `.env.example` carries placeholders, and
`Settings.validate_for_production` refuses to start a production process whose
`SECRET_KEY` or database password is missing, too short, or a known
placeholder — and refuses to run with `APP_DEBUG=true`.

Interactive API docs are disabled in production: the schema describes every
financial endpoint.

## Errors

A `DomainError` maps to a uniform JSON envelope with a stable machine `code`,
so the frontend localises the message itself.

An unexpected exception returns a generic 500 carrying only the request id.
Internal messages and stack traces never reach a client.

404 is used where 403 would confirm existence, so record identifiers cannot be
probed across tenants.

## Clinical data

Clinical treatment detail is separated from financial detail. Financial staff
get **no** clinical access by default — a test asserts that `accountant`,
`cashier`, `auditor` and `receptionist` hold neither `clinic.clinical.read`
nor `clinic.clinical.write`. Only the dentist role reads clinical records, and
`clinic.clinical.read` is marked sensitive so such reads are audited.

Patient health data may carry obligations beyond ordinary business records.
That needs a regulatory review this project is not qualified to perform; it is
`OPEN_QUESTIONS.md` question C1, and the clinic module is not built until it
is answered.

## Backups and retention

**Not yet implemented.** Phase 7, and `OPEN_QUESTIONS.md` question D1 asks for
the retention period, backup location and who may restore.

A backup that has never been restored is not a backup. Phase 7 includes a
restore rehearsal against a scratch database, verifying the restored trial
balance matches the source.

## Not yet done

Stated plainly rather than implied:

| Item | Phase |
| --- | --- |
| Safe file upload handling (type, size, scanning) | 7, with the document module |
| Backup and verified restore | 7 |
| CSRF defences | Not applicable while auth is a bearer token in memory; required if cookie sessions are ever adopted |
| Multi-factor authentication | Not planned; would be the next step if the system becomes internet-facing |
| Dependency vulnerability scanning in CI | Phase 7 |
| Penetration test | Phase 7, before go-live |

## Reporting a problem

This system holds a small business's financial records and its patients'
details. If you find a security problem, tell the system owner directly rather
than filing it in a tracker.
