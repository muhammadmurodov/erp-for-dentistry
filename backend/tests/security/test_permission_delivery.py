"""A permission in the code must actually reach the database.

The defect this guards against was invisible to every other test in this
suite, and that is the point worth remembering. `permission` and the system
roles' grants are seeded by migration `0001`, which reads
`app.core.permissions.PERMISSIONS` at migration time — so on a database built
from `base`, which is what every test run does, the catalogue and the database
always agree. On a database migrated before a permission was written, nothing
delivers it: not a later migration, not a reconciliation at startup.

Two permissions were therefore dead in every existing deployment.
`sales.debt.write_off` shipped with its route, its service and its audit
event, and no role in a real database held it. `organization.duties.declare`
made the duty-separation declaration unreachable — the owner was refused their
own setting with a 403. Both were found by driving a browser against a
database that had been migrated months earlier, and migration `0011` delivers
them.

So there are two kinds of check here. The ones against the database confirm
that the migration chain covers the catalogue. The frozen list confirms that
somebody adding a permission is made to think about delivering it, which is
the part a fresh test database can never notice.
"""

from __future__ import annotations

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import (
    ALL_PERMISSION_CODES,
    PERMISSIONS,
    SYSTEM_ROLES,
    SYSTEM_ROLES_BY_CODE,
)
from app.modules.identity.models import Permission

#: Every permission the application declares, frozen.
#:
#: **Adding a permission means adding a data migration that delivers it to
#: databases already in service, and updating this list in the same commit.**
#: Seeding happens once, in `0001`; a catalogue entry with no migration behind
#: it works on your machine, passes this suite, and is missing in production.
#: `0011` is the worked example of the migration to copy.
DECLARED_PERMISSIONS: frozenset[str] = frozenset(
    {
        "accounting.account.manage",
        "accounting.account.read",
        "accounting.journal.draft",
        "accounting.journal.post",
        "accounting.journal.read",
        "accounting.journal.reverse",
        "accounting.policy.manage",
        "accounting.policy.read",
        "audit.log.read",
        "banking.account.manage",
        "banking.account.read",
        "banking.reconcile",
        "banking.statement.import",
        "cash.count.submit",
        "cash.discrepancy.approve",
        "cash.register.manage",
        "cash.register.read",
        "cash.session.operate",
        "clinic.appointment.manage",
        "clinic.appointment.read",
        "clinic.clinical.read",
        "clinic.clinical.write",
        "clinic.patient.manage",
        "clinic.patient.read",
        "clinic.treatment.complete",
        "document.manage",
        "document.read",
        "expense.request.approve",
        "expense.request.read",
        "expense.request.submit",
        "hr.attendance.record",
        "hr.employee.manage",
        "hr.employee.read",
        "identity.role.manage",
        "identity.role.read",
        "identity.user.manage",
        "identity.user.read",
        "inventory.adjustment.approve",
        "inventory.consumption.record",
        "inventory.item.manage",
        "inventory.item.read",
        "inventory.receipt.record",
        "organization.duties.declare",
        "organization.manage",
        "organization.period.manage",
        "organization.period.read",
        "organization.read",
        "payroll.rules.manage",
        "payroll.run.approve",
        "payroll.run.calculate",
        "payroll.run.post",
        "payroll.run.read",
        "purchasing.bill.manage",
        "purchasing.order.approve",
        "purchasing.order.manage",
        "purchasing.order.read",
        "purchasing.payment.record",
        "purchasing.supplier.manage",
        "purchasing.supplier.read",
        "reporting.financial.read",
        "reporting.management.read",
        "sales.debt.write_off",
        "sales.invoice.manage",
        "sales.invoice.read",
        "sales.payment.record",
        "sales.refund.approve",
        "sales.refund.request",
        "sales.service.manage",
        "sales.service.read",
    }
)


def _load_migration_0011():
    """Load the delivery migration as a module.

    `alembic/versions` is a directory of scripts, not a package, so it is
    loaded by path. Worth the small awkwardness: the alternative is a frozen
    list nothing checks, and a frozen list that has silently drifted from the
    code it claims to deliver is worse than not having one.
    """
    import importlib.util
    import pathlib

    path = (
        pathlib.Path(__file__).resolve().parents[2]
        / "alembic"
        / "versions"
        / "0011_deliver_late_permissions.py"
    )
    spec = importlib.util.spec_from_file_location("migration_0011", path)
    assert spec and spec.loader, f"cannot load {path}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestTheCatalogueIsDeclared:
    def test_nothing_was_added_without_being_declared(self) -> None:
        added = ALL_PERMISSION_CODES - DECLARED_PERMISSIONS
        assert not added, (
            f"new permission(s) {sorted(added)} are in the catalogue but not declared here. "
            "Seeding happens once, in migration 0001: a permission added later needs a data "
            "migration to deliver it to databases already in service (see 0011), and this "
            "list updated in the same commit. Without that it is missing in production and "
            "every test still passes."
        )

    def test_nothing_was_removed_without_being_undeclared(self) -> None:
        removed = DECLARED_PERMISSIONS - ALL_PERMISSION_CODES
        assert not removed, (
            f"permission(s) {sorted(removed)} are declared here but no longer in the "
            "catalogue. Withdrawing one is a deliberate act: somebody may hold it, and the "
            "foreign key from role_permission will refuse to let the row go."
        )

    def test_every_permission_belongs_to_a_module_and_says_what_it_does(self) -> None:
        for permission in PERMISSIONS:
            assert permission.module, f"{permission.code} names no module"
            assert permission.description.strip(), f"{permission.code} has no description"


class TestTheMigrationChainDeliversThem:
    """Against the migrated database, which is what a deployment has."""

    async def test_every_declared_permission_has_a_row(self, db: AsyncSession) -> None:
        rows = (await db.execute(select(Permission.code))).scalars().all()
        in_database = set(rows)
        missing = ALL_PERMISSION_CODES - in_database
        assert not missing, (
            f"{sorted(missing)} exist in the catalogue but not in the database after "
            "`alembic upgrade head`. A role cannot be granted a permission with no row: "
            "role_permission has a foreign key to it."
        )

    async def test_the_database_holds_nothing_the_catalogue_does_not(
        self, db: AsyncSession
    ) -> None:
        rows = (await db.execute(select(Permission.code))).scalars().all()
        orphans = set(rows) - ALL_PERMISSION_CODES
        assert not orphans, (
            f"{sorted(orphans)} are in the database but not in the catalogue. The "
            "application would never check them, so a role holding one carries authority "
            "nothing can see."
        )

    async def test_each_system_role_holds_exactly_what_it_defines(
        self, db: AsyncSession
    ) -> None:
        """Both directions. A missing grant is a dead feature; an extra one is
        authority nobody wrote down — and the `director` role holding both
        sides of a purchasing control is a defect this project has already
        had once."""
        for role in SYSTEM_ROLES:
            granted = set(
                (
                    await db.execute(
                        text(
                            """
                            SELECT rp.permission_code
                            FROM role_permission rp
                            JOIN role r ON r.id = rp.role_id
                            WHERE r.code = :code AND r.organization_id IS NULL
                            """
                        ),
                        {"code": role.code},
                    )
                )
                .scalars()
                .all()
            )
            expected = set(role.permissions)
            assert granted == expected, (
                f"the {role.code} role's grants in the database do not match its "
                f"definition: missing {sorted(expected - granted)}, "
                f"unexpected {sorted(granted - expected)}"
            )

    async def test_the_owner_alone_may_declare_duty_separation(
        self, db: AsyncSession
    ) -> None:
        """The regression that started this file: the owner was refused their
        own setting with a 403, because the permission had a route and a check
        but no row and no grant."""
        holders = (
            (
                await db.execute(
                    text(
                        """
                        SELECT r.code
                        FROM role_permission rp
                        JOIN role r ON r.id = rp.role_id
                        WHERE rp.permission_code = 'organization.duties.declare'
                          AND r.organization_id IS NULL
                        """
                    )
                )
            )
            .scalars()
            .all()
        )
        assert set(holders) == {"owner"}, (
            f"organization.duties.declare is held by {sorted(holders)}; it is the owner's "
            "alone (ADR-041)"
        )

    async def test_the_write_off_permission_reaches_the_roles_that_need_it(
        self, db: AsyncSession
    ) -> None:
        """The other half of the same defect, shipped a commit earlier."""
        holders = (
            (
                await db.execute(
                    text(
                        """
                        SELECT r.code
                        FROM role_permission rp
                        JOIN role r ON r.id = rp.role_id
                        WHERE rp.permission_code = 'sales.debt.write_off'
                          AND r.organization_id IS NULL
                        """
                    )
                )
            )
            .scalars()
            .all()
        )
        assert set(holders) == {"owner", "director"}, (
            f"sales.debt.write_off is held by {sorted(holders)}; the accountant is "
            "deliberately excluded and the owner and director are not"
        )


class TestTheDeliveryMigrationDescribesTheCatalogueTruthfully:
    """`0011`'s list is frozen, so it must be checked against the code it
    claims to deliver — a frozen list that has drifted is worse than none."""

    def test_its_permissions_exist_and_match_their_catalogue_entry(self) -> None:
        late = _load_migration_0011()
        by_code = {p.code: p for p in PERMISSIONS}
        for code, module, description, sensitive in late.LATE_PERMISSIONS:
            assert code in by_code, f"0011 delivers {code}, which the catalogue does not define"
            entry = by_code[code]
            assert entry.module == module
            assert entry.description == description
            assert entry.sensitive == sensitive

    def test_its_grants_match_the_role_definitions(self) -> None:
        late = _load_migration_0011()
        for role_code, permission_code in late.LATE_GRANTS:
            role = SYSTEM_ROLES_BY_CODE[role_code]
            assert permission_code in role.permissions, (
                f"0011 grants {permission_code} to {role_code}, which its definition "
                "does not include"
            )
