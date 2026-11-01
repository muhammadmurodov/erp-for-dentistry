"""deliver the permissions added after 0001 to databases already migrated

A defect found by driving the application in a browser against a database
that had been migrated months earlier, which is the only way it *could* be
found: every automated suite rebuilds the database from `base` on each run, so
the catalogue and the database always agreed there.

`permission` and the system roles' grants are seeded by `0001`, which reads
`app.core.permissions.PERMISSIONS` at migration time. On a fresh database that
is always current. On a database migrated before a permission existed, nothing
ever delivers it — there is no later migration and no reconciliation at
startup. Two permissions were therefore unreachable in every existing
deployment:

* `sales.debt.write_off`, added for Phase 3c. The write-off operation, its
  route and its audit event all shipped, and no role in an existing database
  held the permission, so nobody could use it. The tests passed because they
  ran against a database built from the current catalogue.
* `organization.duties.declare`, added for ADR-041, which made the whole
  duty-separation declaration unreachable: the owner was not shown the card
  and the endpoint refused the owner with a 403.

This migration delivers both, additively and idempotently.

**The list below is frozen on purpose.** It does not read the live catalogue:
a data migration that reads today's code says something different depending on
when it runs, which is the bug being fixed. A permission added after this
revision needs its own migration, and
`tests/unit/test_permission_delivery.py` fails until it has one.

Nothing is removed. Withdrawing a permission is a deliberate act with its own
consequences for anybody holding it, not a side effect of reconciliation.

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-01
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


#: (code, module, description, is_sensitive) — frozen at this revision.
LATE_PERMISSIONS: tuple[tuple[str, str, str, bool], ...] = (
    (
        "sales.debt.write_off",
        "sales",
        "Write off an uncollectable patient debt",
        True,
    ),
    (
        "organization.duties.declare",
        "organization",
        "Declare that the organization has too few people to separate duties",
        True,
    ),
)

#: Which system roles hold them. Frozen for the same reason.
LATE_GRANTS: tuple[tuple[str, str], ...] = (
    ("owner", "sales.debt.write_off"),
    ("director", "sales.debt.write_off"),
    # Deliberately the owner alone: declaring that duties cannot be separated
    # decides whether an approval control applies to the person at the till or
    # to somebody standing behind them, and there is nobody above the owner to
    # ask (ADR-041).
    ("owner", "organization.duties.declare"),
)


def upgrade() -> None:
    bind = op.get_bind()
    for code, module, description, is_sensitive in LATE_PERMISSIONS:
        bind.execute(
            sa.text(
                """
                INSERT INTO permission (code, module, description, is_sensitive)
                VALUES (:code, :module, :description, :is_sensitive)
                ON CONFLICT (code) DO NOTHING
                """
            ),
            {
                "code": code,
                "module": module,
                "description": description,
                "is_sensitive": is_sensitive,
            },
        )
    for role_code, permission_code in LATE_GRANTS:
        # Scoped to the system role (organization_id IS NULL). A tenant's own
        # role of the same code is its own business and is left alone.
        bind.execute(
            sa.text(
                """
                INSERT INTO role_permission (role_id, permission_code)
                SELECT r.id, :permission_code
                FROM role r
                WHERE r.code = :role_code AND r.organization_id IS NULL
                ON CONFLICT (role_id, permission_code) DO NOTHING
                """
            ),
            {"role_code": role_code, "permission_code": permission_code},
        )


def downgrade() -> None:
    # Intentionally not reversed. These rows are derived from the application's
    # own permission catalogue, not user data: removing them from a database
    # whose application still declares them would take the write-off operation
    # and the duty-separation declaration away from the owner, which is the
    # defect this migration exists to fix. Going back to 0010 leaves them in
    # place, where they are harmless — 0001 would have seeded them on any
    # database created after they were written.
    pass
