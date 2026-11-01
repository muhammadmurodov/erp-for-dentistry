"""an organization may declare that it cannot separate duties

A clinic run by two or three family members cannot put a different person on
each side of an approval control. Until now only the *owner* was exempt
(ADR-018), which left a real dead end: an employee who counted the till and
found it short could not close the session at all, and the drawer stayed open
until the owner came in. The control did not produce a second person; it
produced an open drawer, and — eventually — a shared login, which is the
outcome that actually destroys an audit trail.

So the exemption becomes something the organization states about itself, once,
on the record: `duties_cannot_be_separated`, with the date it was last
declared. Who declared it, and why, is in the audit trail under
`organization.duties.declared`, which is the authority — a column can be
updated, an audit row cannot.

It grants nobody a permission. It removes only the requirement that an
approver be a *different* person, and every act relying on it is marked
`self_approved` where it is recorded (ADR-041).

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-01
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "organization",
        sa.Column(
            "duties_cannot_be_separated",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "organization",
        sa.Column("duties_declared_at", sa.DateTime(timezone=True), nullable=True),
    )
    # A declaration with no date would leave nothing to reconcile the audit
    # trail against, and "when was this last looked at" is the first question
    # a reviewer asks about a relaxed control.
    # `op.f` marks the name as already conventional: without it the metadata
    # naming convention prefixes `ck_organization_` a second time, and the
    # downgrade then cannot find what the upgrade created.
    op.create_check_constraint(
        op.f("ck_organization_duty_declaration_has_a_date"),
        "organization",
        "duties_cannot_be_separated IS FALSE OR duties_declared_at IS NOT NULL",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_organization_duty_declaration_has_a_date"), "organization")
    op.drop_column("organization", "duties_declared_at")
    op.drop_column("organization", "duties_cannot_be_separated")
