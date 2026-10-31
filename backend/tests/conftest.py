"""Test fixtures.

Each test runs against a real PostgreSQL database, because most of what this
project must guarantee — composite foreign keys, check constraints, the
append-only audit trigger — does not exist in SQLite. A fake would pass tests
the real system fails.

The schema is created once per session by running the Alembic migrations, so
the tests exercise the same DDL that production will receive. Every test then
runs inside a transaction that is rolled back, which keeps them independent
and order-free.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator, Iterator
from decimal import Decimal

import pytest
import pytest_asyncio

# Point the application at the test database before any app module reads
# settings. `TEST_POSTGRES_DB` lets CI override the name.
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("POSTGRES_DB", os.environ.get("TEST_POSTGRES_DB", "erp_test"))
os.environ.setdefault("SECRET_KEY", "test-secret-key-not-used-outside-tests-0123456789")
os.environ.setdefault("LOG_LEVEL", "WARNING")
os.environ.setdefault("LOG_FORMAT", "console")

from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.core.db import get_db_session, reset_engine_for_tests
from app.core.rate_limit import InMemoryRateLimiter, set_limiter
from app.main import create_app
from app.modules.audit.service import AuditRecorder
from app.modules.identity.service import IdentityService
from app.modules.provisioning.service import ProvisioningService


def _run_migrations() -> None:
    """Rebuild the test schema from the migration history."""
    from alembic.config import Config

    from alembic import command

    settings = get_settings()
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", settings.sync_database_url)
    # Silence the post-write hooks; they are for authoring, not for running.
    command.downgrade(config, "base")
    command.upgrade(config, "head")


@pytest.fixture(scope="session", autouse=True)
def _schema() -> Iterator[None]:
    """Rebuild the schema once for the whole run."""
    _run_migrations()
    yield


@pytest_asyncio.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    """A per-test engine.

    Function scope rather than session scope: pytest-asyncio gives each test
    its own event loop, and an engine's connection pool cannot be shared
    across loops. `NullPool` keeps that cheap by not holding connections open.
    """
    settings = get_settings()
    eng = create_async_engine(
        settings.database_url,
        poolclass=NullPool,
        connect_args={"statement_cache_size": 0},
    )
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def db(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """A session wrapped in a transaction that is always rolled back.

    Services under test call `commit()`; binding the session to an outer
    connection-level transaction means that commit is a savepoint release,
    and the outer rollback still discards everything.
    """
    async with engine.connect() as connection:
        outer = await connection.begin()
        factory = async_sessionmaker(
            bind=connection,
            expire_on_commit=False,
            autoflush=False,
            join_transaction_mode="create_savepoint",
        )
        async with factory() as session:
            yield session
        await outer.rollback()


@pytest.fixture(autouse=True)
def _reset_global_engine() -> Iterator[None]:
    """Give each test its own engine, bound to its own event loop."""
    reset_engine_for_tests()
    yield
    reset_engine_for_tests()


@pytest.fixture(autouse=True)
def _reset_rate_limiter() -> Iterator[None]:
    limiter = InMemoryRateLimiter()
    set_limiter(limiter)
    yield
    limiter.reset()


@pytest.fixture
def audit(db: AsyncSession) -> AuditRecorder:
    return AuditRecorder(db, actor_label="test-suite")


@pytest_asyncio.fixture
async def client(db: AsyncSession) -> AsyncIterator[AsyncClient]:
    """HTTP client whose requests share the test's transaction."""
    app = create_app()

    async def _override() -> AsyncIterator[AsyncSession]:
        # Mirrors `app.core.db.get_db_session`, including the rollback on
        # error. Without that rollback a test would silently pass for work
        # that production discards.
        try:
            yield db
        except Exception:
            await db.rollback()
            raise

    app.dependency_overrides[get_db_session] = _override
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as http_client:
        yield http_client
    app.dependency_overrides.clear()


# --- domain fixtures --------------------------------------------------------

OWNER_PASSWORD = "fixture-owner-password-1"
STAFF_PASSWORD = "fixture-staff-password-1"


class OrganizationFixture:
    """A seeded organization plus handles to its people."""

    def __init__(self, organization, owner, owner_membership) -> None:
        self.organization = organization
        self.id = organization.id
        self.owner = owner
        self.owner_membership = owner_membership
        self.members: dict[str, tuple[object, object]] = {}


async def _make_organization(
    db: AsyncSession, audit: AuditRecorder, *, code: str, roles: dict[str, list[str]]
) -> OrganizationFixture:
    provisioning = ProvisioningService(db, audit)
    organization, owner, owner_membership = await provisioning.create_organization_with_owner(
        code=code,
        legal_name=f'"{code}" MChJ',
        short_name=code,
        owner_email=f"owner@{code}.example.com",
        owner_full_name=f"Owner of {code}",
        owner_password=OWNER_PASSWORD,
    )
    fixture = OrganizationFixture(organization, owner, owner_membership)

    identity = IdentityService(db, audit)
    for label, role_codes in roles.items():
        user, membership = await identity.create_user_in_organization(
            organization_id=organization.id,
            email=f"{label}@{code}.example.com",
            full_name=f"{label.title()} of {code}",
            password=STAFF_PASSWORD,
            role_codes=role_codes,
            must_change_password=False,
        )
        fixture.members[label] = (user, membership)
    await db.flush()
    return fixture


@pytest_asyncio.fixture
async def org_a(db: AsyncSession, audit: AuditRecorder) -> OrganizationFixture:
    return await _make_organization(
        db,
        audit,
        code=f"clinic-a-{uuid.uuid4().hex[:6]}",
        roles={
            "accountant": ["accountant"],
            "cashier": ["cashier"],
            "auditor": ["auditor"],
            "receptionist": ["receptionist"],
        },
    )


@pytest_asyncio.fixture
async def org_b(db: AsyncSession, audit: AuditRecorder) -> OrganizationFixture:
    """A second tenant. Nothing in `org_a` may ever be reachable from here."""
    return await _make_organization(
        db,
        audit,
        code=f"clinic-b-{uuid.uuid4().hex[:6]}",
        roles={"accountant": ["accountant"]},
    )


@pytest_asyncio.fixture
async def login(client: AsyncClient):
    """Sign in and return a callable producing authorization headers."""

    async def _login(email: str, password: str, organization_id=None) -> dict[str, str]:
        payload: dict[str, object] = {"email": email, "password": password}
        if organization_id is not None:
            payload["organization_id"] = str(organization_id)
        response = await client.post("/api/v1/auth/login", json=payload)
        assert response.status_code == 200, response.text
        return {"Authorization": f"Bearer {response.json()['access_token']}"}

    return _login


@pytest_asyncio.fixture
async def truncate_check(db: AsyncSession):
    """Helper asserting a table holds no rows for an organization."""

    async def _count(table: str, organization_id) -> int:
        result = await db.execute(
            text(f"SELECT count(*) FROM {table} WHERE organization_id = :org"),
            {"org": organization_id},
        )
        return int(result.scalar_one())

    return _count


@pytest_asyncio.fixture
async def committed_org(engine: AsyncEngine) -> AsyncIterator[OrganizationFixture]:
    """An organization that is actually committed to the database.

    Needed by tests of behaviour that writes in its *own* transaction — a
    denied-permission audit record, for example. Such a write happens on a
    separate connection and therefore cannot see rows that the usual
    rolled-back test transaction is holding.

    The rows stay behind: `audit_log` is append-only by design, so audit rows
    referencing this organization cannot be deleted and the organization
    cannot be deleted while they reference it. The session-scoped schema
    rebuild is what clears them. Tests must therefore stay scoped to their own
    organization and never assume an empty table.
    """
    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    async with factory() as session:
        fixture = await _make_organization(
            session,
            AuditRecorder(session, actor_label="test-suite"),
            code=f"committed-{uuid.uuid4().hex[:6]}",
            roles={"cashier": ["cashier"]},
        )
        await session.commit()
        # Detach: the session closes here, and the caller only reads ids and
        # emails that are already loaded.
        yield fixture


@pytest_asyncio.fixture
async def committed_client(engine: AsyncEngine) -> AsyncIterator[AsyncClient]:
    """HTTP client using real, committing sessions rather than the test one."""
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as http_client:
        yield http_client


# --- accounting fixtures ----------------------------------------------------

#: A deliberately tiny chart. Real organizations seed the 368-row statutory
#: one; a test that needs five accounts should not pay for 368.
TEST_ACCOUNTS = (
    # (code, name, level, nature, element)
    ("5000", "Cash accounts", "GROUP", "ASSET", "ASSET"),
    ("5010", "Cash in hand", "ACCOUNT", "ASSET", "ASSET"),
    ("5110", "Bank current account", "ACCOUNT", "ASSET", "ASSET"),
    ("5110.03", "Card settlement receivable", "SUBACCOUNT", "TRANSIT", "ASSET"),
    ("4010", "Customer receivables", "ACCOUNT", "ASSET", "ASSET"),
    ("6310", "Advances received from customers", "ACCOUNT", "LIABILITY_EQUITY", "LIABILITY"),
    ("9030", "Revenue from services", "ACCOUNT", "LIABILITY_EQUITY", "REVENUE"),
    ("9420", "Administrative expenses", "ACCOUNT", "ASSET", "EXPENSE"),
    ("9910", "Profit and loss summary", "ACCOUNT", "ASSET", "RESULT"),
)


class LedgerFixture:
    """An organization with a calendar and a chart, ready to post into."""

    def __init__(self, org, fiscal_year, periods, accounts) -> None:
        self.org = org
        self.id = org.id
        self.fiscal_year = fiscal_year
        self.periods = periods
        self.accounts = accounts

    def period(self, code: str):
        return next(p for p in self.periods if p.code == code)

    def account(self, code: str):
        return self.accounts[code]


@pytest_asyncio.fixture
async def ledger(db: AsyncSession, audit: AuditRecorder, org_a) -> LedgerFixture:
    """`org_a` plus a 2026 fiscal year and a small chart of accounts."""
    from datetime import date

    from app.modules.accounting.enums import AccountElement, AccountLevel, AccountNature
    from app.modules.accounting.services.accounts import AccountService
    from app.modules.accounting.services.periods import PeriodService

    periods_service = PeriodService(db, audit)
    year = await periods_service.create_fiscal_year(
        organization_id=org_a.id,
        code="2026",
        starts_on=date(2026, 1, 1),
        ends_on=date(2026, 12, 31),
    )
    periods = await periods_service.list_periods(org_a.id, year.id)

    accounts_service = AccountService(db, audit)
    accounts = {}
    for code, name, level, nature, element in TEST_ACCOUNTS:
        accounts[code] = await accounts_service.create_account(
            organization_id=org_a.id,
            code=code,
            name=name,
            level=AccountLevel(level),
            nature=AccountNature(nature),
            element=AccountElement(element),
        )
    await db.flush()
    return LedgerFixture(org_a.organization, year, periods, accounts)


@pytest.fixture
def posting(db: AsyncSession, audit: AuditRecorder):
    """A `PostingService` bound to the test session."""
    from app.modules.accounting.services.posting import PostingService

    return PostingService(db, audit)


# --- sales fixtures ---------------------------------------------------------


class SalesFixture:
    """A ledger with a chart, an active policy, a customer and a service."""

    def __init__(self, ledger, customer, service) -> None:
        self.ledger = ledger
        self.id = ledger.id
        self.customer = customer
        self.service = service


@pytest_asyncio.fixture
async def sales(db: AsyncSession, audit: AuditRecorder, org_a) -> SalesFixture:
    """Everything an invoice needs: calendar, chart, policy, customer, service.

    Uses the *statutory* chart rather than the handful of accounts the
    `ledger` fixture creates, because the default policy maps to real account
    codes (4010, 6310, 9030 and so on) and the point is to exercise them.
    """
    from datetime import date

    from app.modules.accounting.services.accounts import AccountService
    from app.modules.accounting.services.default_policy import DefaultPolicyService
    from app.modules.accounting.services.periods import PeriodService
    from app.modules.accounting.services.policy import PolicyService
    from app.modules.sales.services.catalogue import CatalogueService

    periods = PeriodService(db, audit)
    year = await periods.create_fiscal_year(
        organization_id=org_a.id,
        code="2026",
        starts_on=date(2026, 1, 1),
        ends_on=date(2026, 12, 31),
    )
    period_rows = await periods.list_periods(org_a.id, year.id)

    await AccountService(db, audit).seed_statutory_chart(organization_id=org_a.id)

    policy = await DefaultPolicyService(db, audit).seed(
        organization_id=org_a.id, effective_from=date(2026, 1, 1)
    )
    await PolicyService(db, audit).activate(organization_id=org_a.id, policy_id=policy.id)

    catalogue = CatalogueService(db, audit)
    customer = await catalogue.create_customer(
        organization_id=org_a.id,
        code="C-0001",
        display_name="Demo Patient",
    )
    service = await catalogue.create_service(
        organization_id=org_a.id,
        code="S-0001",
        name="Pulpit treatment, single canal",
        price=Decimal("300000"),
        effective_from=date(2026, 1, 1),
        mxik_code="10902003047000000",
    )
    await db.flush()

    ledger = LedgerFixture(org_a.organization, year, period_rows, {})
    return SalesFixture(ledger, customer, service)


@pytest.fixture
def billing(db: AsyncSession, audit: AuditRecorder):
    from app.modules.sales.services.billing import BillingService

    return BillingService(db, audit)


# --- cash fixtures ----------------------------------------------------------


@pytest_asyncio.fixture
async def till(db: AsyncSession, audit: AuditRecorder, sales):
    """A cash register on account 5010, in the `sales` organization."""
    from app.modules.cash.services.sessions import CashSessionService

    return await CashSessionService(db, audit).create_register(
        organization_id=sales.id,
        code="TILL-1",
        name="Reception till",
        account_code="5010",
    )


@pytest.fixture
def cash(db: AsyncSession, audit: AuditRecorder):
    from app.modules.cash.services.sessions import CashSessionService

    return CashSessionService(db, audit)


# --- banking fixtures -------------------------------------------------------


@pytest_asyncio.fixture
async def bank(db: AsyncSession, audit: AuditRecorder, sales):
    """A bank account on 5110.01, in the `sales` organization."""
    from app.modules.banking.services.accounts import BankAccountService

    return await BankAccountService(db, audit).create(
        organization_id=sales.id,
        code="BANK-1",
        name="Settlement account",
        account_code="5110.01",
        bank_name="Demo Bank",
        mfo="00414",
        account_number="20208000900123456789",
    )


@pytest.fixture
def reconciliation(db: AsyncSession, audit: AuditRecorder):
    from app.modules.banking.services.reconciliation import ReconciliationService

    return ReconciliationService(db, audit)


@pytest.fixture
def importer(db: AsyncSession, audit: AuditRecorder):
    from app.modules.banking.services.statement_import import StatementImporter

    return StatementImporter(db, audit)
