# Business ERP

A modular business management platform for small businesses in Uzbekistan,
built around a double-entry accounting engine. The first tenant is a dental
clinic registered as an MChJ, but the accounting core is deliberately generic:
clinic-specific workflows live in their own module and reach the ledger only
through a published service interface.

**Current state: phases 1, 2 and 3 complete and verified — the foundation, the
accounting engine, and sales, cash and banking on top of it. The clinic module
(3c), purchasing, payroll and statutory reporting are not built yet.**
`docs/IMPLEMENTATION_STATUS.md` is the authoritative list of what works, down
to which features have no interface; the application's own navigation marks
unfinished modules rather than presenting them as done.

## What exists today

| Area | State |
| --- | --- |
| Organizations, multi-tenancy, strict data isolation | Working |
| Authentication, refresh-token rotation, account lockout | Working |
| Roles, granular permissions, segregation-of-duties controls | Working |
| Append-only audit trail (enforced by the database) | Working |
| Application shell, Uzbek/Russian/English interface | Working |
| Chart of accounts, journals, posting, trial balance, statements | Working |
| Customers, service catalogue, invoices, payments, advances, ageing | Working |
| Cash registers, sessions, the daily close and its discrepancy | Working |
| Bank accounts, statement import, matching, reconciliation | Working |
| Clinic, purchasing, inventory, payroll | **Not built — Phases 3c–5** |
| Statutory reports, dashboards | **Not built — Phase 6** |

## Quick start

Requires Docker and Docker Compose. For local work without containers you also
need Python 3.11+, Node 22+, PostgreSQL 16+ and [uv](https://docs.astral.sh/uv/).

```bash
cp .env.example .env
# Generate a real secret key — the example value is refused outside development:
python3 -c "import secrets; print('SECRET_KEY=' + secrets.token_urlsafe(64))"
# Edit .env: paste that SECRET_KEY and set POSTGRES_PASSWORD.

docker compose up -d --build
```

The API is then on <http://localhost:8000> (docs at `/docs` outside
production), the interface on <http://localhost:5173>.

### Without Docker

```bash
make setup            # backend venv + frontend dependencies
createdb erp_dev      # adjust to match your .env
make migrate
make seed             # fictional demo organization
make dev-backend      # terminal 1
make dev-frontend     # terminal 2
```

`make seed` prints sign-in credentials for one fictional account per role
(owner, accountant, cashier, receptionist, dentist, auditor) so each
permission set can be exercised by hand. They are development-only and the
command refuses to run when `APP_ENV=production`.

## Verifying it

```bash
make check            # lint + full test suite, both sides
make test-backend     # 153 backend tests
```

The backend tests run against a real PostgreSQL instance, because much of what
the system guarantees — composite foreign keys that make a cross-tenant row
unrepresentable, the trigger that makes the audit trail append-only — does not
exist in SQLite. A fake database would pass tests the real system fails.

## Layout

```
backend/
  app/
    core/            Config, database, money, permissions, logging, errors
    modules/         Bounded business modules (see docs/ARCHITECTURE.md)
    reference/       Statutory reference data and its loaders
    api/             HTTP layer: routers, dependencies, middleware
  alembic/           Migrations
  tests/             unit / integration / security
frontend/
  src/
    components/      Shared UI vocabulary
    features/        One directory per screen area
    i18n/            Centralised translations (uz, ru, en)
    lib/             API client, query hooks, types
docs/                Architecture, accounting design, decisions, open questions
infra/               Database initialisation
reference/           The legacy workbook (untracked; see below)
```

## Documentation

| Document | What it covers |
| --- | --- |
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | Module boundaries, dependency rules, request flow |
| [ACCOUNTING_DESIGN.md](docs/ACCOUNTING_DESIGN.md) | The double-entry engine, its invariants, how business events post |
| [DATABASE_DESIGN.md](docs/DATABASE_DESIGN.md) | Schema, constraints, concurrency, money representation |
| [BUSINESS_PROCESSES.md](docs/BUSINESS_PROCESSES.md) | The clinic's workflows, with diagrams |
| [LEGACY_WORKBOOK_MAPPING.md](docs/LEGACY_WORKBOOK_MAPPING.md) | What the Excel workbook contains and where each concept lands |
| [OPEN_QUESTIONS.md](docs/OPEN_QUESTIONS.md) | **Decisions needed from the owner and accountant** |
| [SECURITY.md](docs/SECURITY.md) | Authentication, authorization, tenant isolation, data retention |
| [TESTING.md](docs/TESTING.md) | Test strategy and how to run each suite |
| [DECISIONS.md](docs/DECISIONS.md) | Architectural decisions and their reasoning |
| [IMPLEMENTATION_STATUS.md](docs/IMPLEMENTATION_STATUS.md) | Feature-by-feature state against the definition of done |

## The reference workbook

`reference/` holds the clinic's existing macro-enabled workbook. It is
**untracked** (`.gitignore`, enforced by a CI check) because it contains the
clinic's bank account numbers, its director's name, employee salary history
and patient-level fiscal receipts.

It is treated as evidence of current practice, not as a specification. Its
historical tax rates are **not** carried into payroll logic, and its
debit/credit mappings are documented as findings to confirm rather than
transcribed as rules. Statutory reference material that is public legislation
— the national chart of accounts, the timesheet code list — is extracted into
`backend/app/reference/data/` with its provenance and its unverified parts
flagged.

The workbook is never modified, and its macros are never executed.

## A note on scope

Accounting classifications, statutory report formats and payroll tax rules
must be reviewed by a qualified Uzbek accountant before this system keeps real
books. The project makes no compliance claim. Everything that needs such a
review is listed in [OPEN_QUESTIONS.md](docs/OPEN_QUESTIONS.md).
