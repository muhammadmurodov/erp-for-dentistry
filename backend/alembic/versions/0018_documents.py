"""Documents: the filing cabinet, and the checksum that proves it.

Two tables. `document` holds everything about a stored file except the file:
its category, its title, what business record it is filed against, who
uploaded it, the SHA-256 of its bytes and the storage key those bytes live
under. `document_access_log` holds who read one, and when.

Three decisions are visible in the constraints:

* **The entity link is a pair or nothing.** `ck_document_entity_pair` makes
  `entity_type` and `entity_id` null together. There is no foreign key,
  because one column cannot reference eleven tables; the service checks the
  record exists in this organization before anything is written, and that
  check is the constraint.

* **The same file is not filed twice in the same place.**
  `uq_document_checksum_per_entity` turns a double-click into a 409 rather
  than a second copy of a contract.

* **Nothing is deleted.** There is no deleted status and no delete endpoint:
  a document is archived with a reason, and `ck_document_archived_has_reason`
  makes the reason mandatory. How long a clinic must keep what it was given
  is `OPEN_QUESTIONS.md` D1, and until an answer exists this system does not
  destroy evidence.

The bytes themselves are not in this database. They sit under the configured
`DOCUMENT_STORAGE_PATH`, which must be backed up alongside it — the checksum
here is what makes a restore verifiable, and it can only verify a file that
was also restored.

Revision ID: 0018
Revises: 0017
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "document",
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column(
            "category",
            sa.Enum(
                "CONTRACT",
                "INVOICE",
                "RECEIPT",
                "BANK_STATEMENT",
                "IDENTITY",
                "CLINICAL",
                "PAYROLL",
                "STATUTORY",
                "OTHER",
                name="document_category",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("content_type", sa.String(length=128), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("checksum_sha256", sa.String(length=64), nullable=False),
        sa.Column("storage_key", sa.String(length=512), nullable=False),
        sa.Column(
            "entity_type",
            sa.Enum(
                "PATIENT",
                "EMPLOYEE",
                "INVOICE",
                "SUPPLIER",
                "SUPPLIER_INVOICE",
                "PURCHASE_ORDER",
                "PAYMENT",
                "PAYROLL_RUN",
                "BANK_ACCOUNT",
                "JOURNAL_ENTRY",
                "TREATMENT_PLAN",
                name="document_entity_type",
                native_enum=False,
                length=32,
            ),
            nullable=True,
        ),
        sa.Column("entity_id", sa.UUID(), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "STORED",
                "ARCHIVED",
                "SUPERSEDED",
                name="document_status",
                native_enum=False,
                length=32,
            ),
            server_default="STORED",
            nullable=False,
        ),
        sa.Column("status_reason", sa.Text(), nullable=True),
        sa.Column("replaces_document_id", sa.UUID(), nullable=True),
        sa.Column("uploaded_by_user_id", sa.UUID(), nullable=False),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "checksum_sha256 ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_document_ck_document_checksum_is_hex"),
        ),
        sa.CheckConstraint(
            "status <> 'ARCHIVED' OR status_reason IS NOT NULL",
            name=op.f("ck_document_ck_document_archived_has_reason"),
        ),
        sa.CheckConstraint(
            "(entity_type IS NULL) = (entity_id IS NULL)",
            name=op.f("ck_document_ck_document_entity_pair"),
        ),
        sa.CheckConstraint(
            "byte_size > 0", name=op.f("ck_document_ck_document_byte_size_positive")
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.id"],
            name=op.f("fk_document_organization_id_organization"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["replaces_document_id"],
            ["document.id"],
            name=op.f("fk_document_replaces_document_id_document"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["uploaded_by_user_id"],
            ["app_user.id"],
            name=op.f("fk_document_uploaded_by_user_id_app_user"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_document")),
        sa.UniqueConstraint(
            "organization_id",
            "checksum_sha256",
            "entity_type",
            "entity_id",
            name="uq_document_checksum_per_entity",
        ),
        sa.UniqueConstraint("organization_id", "id", name="uq_document_organization_id_id"),
        sa.UniqueConstraint("storage_key", name="uq_document_storage_key"),
    )
    op.create_index(
        "ix_document_category", "document", ["organization_id", "category"], unique=False
    )
    op.create_index(
        "ix_document_entity",
        "document",
        ["organization_id", "entity_type", "entity_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_document_organization_id"), "document", ["organization_id"], unique=False
    )
    op.create_table(
        "document_access_log",
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("document_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("ip_address", sa.String(length=64), nullable=True),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["document.id"],
            name=op.f("fk_document_access_log_document_id_document"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.id"],
            name=op.f("fk_document_access_log_organization_id_organization"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["app_user.id"],
            name=op.f("fk_document_access_log_user_id_app_user"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_document_access_log")),
    )
    op.create_index(
        op.f("ix_document_access_log_document_id"),
        "document_access_log",
        ["document_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_document_access_log_organization_id"),
        "document_access_log",
        ["organization_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_document_access_log_organization_id"), table_name="document_access_log")
    op.drop_index(op.f("ix_document_access_log_document_id"), table_name="document_access_log")
    op.drop_table("document_access_log")
    op.drop_index(op.f("ix_document_organization_id"), table_name="document")
    op.drop_index("ix_document_entity", table_name="document")
    op.drop_index("ix_document_category", table_name="document")
    op.drop_table("document")
