"""Phase 1 foundation: organizations, identity, access control, audit trail.

Beyond the tables, this migration installs the guarantees that must not depend
on application code being correct:

* `gen_random_uuid()` is available for UUID primary keys.
* The audit trail is append-only, enforced by a trigger that rejects every
  UPDATE and DELETE on `audit_log`. An application bug, or a user with full
  application rights, cannot rewrite history.
* The permission catalogue and the system roles are seeded from
  `app.core.permissions`, so a role grant is checked by a foreign key rather
  than by a hopefully-correct string.

Revision ID: 9218b24fa226
Revises:
Create Date: 2026-10-30
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from app.core.permissions import PERMISSIONS, READ_ONLY_ROLE_CODES, SYSTEM_ROLES

revision: str = "9218b24fa226"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# --- reference data ---------------------------------------------------------

#: Seeded so an organization can be created before any currency admin screen
#: exists. `decimal_places` is the authority for rounding amounts in that
#: currency. UZS carries two statutory minor places (tiyin).
CURRENCIES = (
    ("UZS", "Uzbek som", "O'zbek so'mi", "Узбекский сум", "so'm", 2),
    ("USD", "US dollar", "AQSh dollari", "Доллар США", "$", 2),
    ("EUR", "Euro", "Yevro", "Евро", "\u20ac", 2),
    ("RUB", "Russian rouble", "Rossiya rubli", "Российский рубль", "\u20bd", 2),
)


# --- append-only audit trail ------------------------------------------------

AUDIT_GUARD_FUNCTION = """
CREATE OR REPLACE FUNCTION audit_log_is_append_only()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION
        'audit_log is append-only: % on audit record % is not permitted',
        TG_OP, COALESCE(OLD.id::text, '(unknown)')
        USING ERRCODE = 'restrict_violation';
END;
$$;
"""

AUDIT_GUARD_TRIGGER = """
CREATE TRIGGER trg_audit_log_append_only
BEFORE UPDATE OR DELETE ON audit_log
FOR EACH ROW EXECUTE FUNCTION audit_log_is_append_only();
"""


def _seed_reference_data() -> None:
    bind = op.get_bind()

    bind.execute(
        sa.text(
            """
            INSERT INTO currency
                (code, name_en, name_uz, name_ru, symbol, decimal_places, is_active)
            VALUES (:code, :en, :uz, :ru, :symbol, :dp, true)
            """
        ),
        [
            {"code": c, "en": en, "uz": uz, "ru": ru, "symbol": sym, "dp": dp}
            for c, en, uz, ru, sym, dp in CURRENCIES
        ],
    )

    bind.execute(
        sa.text(
            """
            INSERT INTO permission (code, module, description, is_sensitive)
            VALUES (:code, :module, :description, :is_sensitive)
            """
        ),
        [
            {
                "code": p.code,
                "module": p.module,
                "description": p.description,
                "is_sensitive": p.sensitive,
            }
            for p in PERMISSIONS
        ],
    )

    # System roles are global (organization_id IS NULL) and shared by every
    # tenant, so a new organization has a usable set of roles immediately.
    for role in SYSTEM_ROLES:
        role_id = uuid.uuid4()
        bind.execute(
            sa.text(
                """
                INSERT INTO role
                    (id, organization_id, code, name_uz, name_ru, name_en,
                     description, is_system, is_read_only)
                VALUES
                    (:id, NULL, :code, :name_uz, :name_ru, :name_en,
                     :description, true, :is_read_only)
                """
            ),
            {
                "id": role_id,
                "code": role.code,
                "name_uz": role.name_uz,
                "name_ru": role.name_ru,
                "name_en": role.name_en,
                "description": role.description,
                "is_read_only": role.code in READ_ONLY_ROLE_CODES,
            },
        )
        bind.execute(
            sa.text(
                "INSERT INTO role_permission (role_id, permission_code) "
                "VALUES (:role_id, :permission_code)"
            ),
            [{"role_id": role_id, "permission_code": code} for code in sorted(role.permissions)],
        )


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    # ### schema (generated from the SQLAlchemy models) ###
    op.create_table(
        "app_user",
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("full_name", sa.String(length=255), nullable=False),
        sa.Column("phone", sa.String(length=32), nullable=True),
        sa.Column("locale", sa.String(length=8), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("is_superuser", sa.Boolean(), nullable=False),
        sa.Column("must_change_password", sa.Boolean(), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_login_count", sa.Integer(), nullable=False),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
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
            "position('@' in email) > 1", name=op.f("ck_app_user_email_has_at_sign")
        ),
        sa.CheckConstraint(
            "char_length(full_name) >= 2", name=op.f("ck_app_user_full_name_min_length")
        ),
        sa.CheckConstraint("email = lower(email)", name=op.f("ck_app_user_email_lowercase")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_app_user")),
        sa.UniqueConstraint("email", name=op.f("uq_app_user_email")),
    )
    op.create_table(
        "currency",
        sa.Column("code", sa.String(length=3), nullable=False),
        sa.Column("name_en", sa.String(length=64), nullable=False),
        sa.Column("name_uz", sa.String(length=64), nullable=False),
        sa.Column("name_ru", sa.String(length=64), nullable=False),
        sa.Column("symbol", sa.String(length=8), nullable=True),
        sa.Column("decimal_places", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
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
        sa.CheckConstraint("code = upper(code)", name=op.f("ck_currency_code_uppercase")),
        sa.CheckConstraint(
            "decimal_places BETWEEN 0 AND 6", name=op.f("ck_currency_decimal_places_in_range")
        ),
        sa.PrimaryKeyConstraint("code", name=op.f("pk_currency")),
    )
    op.create_table(
        "organization",
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("legal_name", sa.String(length=255), nullable=False),
        sa.Column("short_name", sa.String(length=128), nullable=True),
        sa.Column("tax_number", sa.String(length=20), nullable=True),
        sa.Column("legal_form", sa.String(length=32), nullable=True),
        sa.Column("activity_description", sa.Text(), nullable=True),
        sa.Column("address", sa.Text(), nullable=True),
        sa.Column("phone", sa.String(length=32), nullable=True),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("registered_on", sa.Date(), nullable=True),
        sa.Column("base_currency", sa.String(length=3), nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column("default_locale", sa.String(length=8), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
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
            "tax_number IS NULL OR tax_number ~ '^[0-9]{9,14}$'",
            name=op.f("ck_organization_tax_number_digits"),
        ),
        sa.CheckConstraint(
            "base_currency = upper(base_currency)",
            name=op.f("ck_organization_base_currency_uppercase"),
        ),
        sa.CheckConstraint("char_length(code) >= 2", name=op.f("ck_organization_code_min_length")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_organization")),
        sa.UniqueConstraint("code", name=op.f("uq_organization_code")),
    )
    op.create_index(
        op.f("ix_organization_tax_number"), "organization", ["tax_number"], unique=False
    )
    op.create_table(
        "permission",
        sa.Column("code", sa.String(length=80), nullable=False),
        sa.Column("module", sa.String(length=32), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("is_sensitive", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("code", name=op.f("pk_permission")),
    )
    op.create_index(op.f("ix_permission_module"), "permission", ["module"], unique=False)
    op.create_table(
        "audit_log",
        sa.Column("organization_id", sa.UUID(), nullable=True),
        sa.Column("actor_user_id", sa.UUID(), nullable=True),
        sa.Column("actor_label", sa.String(length=255), nullable=False),
        sa.Column("action", sa.String(length=96), nullable=False),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column("target_type", sa.String(length=64), nullable=True),
        sa.Column("target_id", sa.String(length=64), nullable=True),
        sa.Column("target_label", sa.String(length=255), nullable=True),
        sa.Column("business_event", sa.String(length=128), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("old_values", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("new_values", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.Column("ip_address", sa.String(length=64), nullable=True),
        sa.Column("user_agent", sa.String(length=255), nullable=True),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.CheckConstraint(
            "outcome IN ('success', 'failure', 'denied')", name=op.f("ck_audit_log_outcome_known")
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["app_user.id"],
            name=op.f("fk_audit_log_actor_user_id_app_user"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.id"],
            name=op.f("fk_audit_log_organization_id_organization"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_log")),
    )
    op.create_index(op.f("ix_audit_log_action"), "audit_log", ["action"], unique=False)
    op.create_index(
        op.f("ix_audit_log_actor_user_id"), "audit_log", ["actor_user_id"], unique=False
    )
    op.create_index(
        op.f("ix_audit_log_business_event"), "audit_log", ["business_event"], unique=False
    )
    op.create_index(op.f("ix_audit_log_occurred_at"), "audit_log", ["occurred_at"], unique=False)
    op.create_index(
        op.f("ix_audit_log_organization_id"), "audit_log", ["organization_id"], unique=False
    )
    op.create_index(
        "ix_audit_log_organization_id_occurred_at",
        "audit_log",
        ["organization_id", "occurred_at"],
        unique=False,
    )
    op.create_index(op.f("ix_audit_log_request_id"), "audit_log", ["request_id"], unique=False)
    op.create_index("ix_audit_log_target", "audit_log", ["target_type", "target_id"], unique=False)
    op.create_table(
        "membership",
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("job_title", sa.String(length=128), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("is_primary_owner", sa.Boolean(), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.id"],
            name=op.f("fk_membership_organization_id_organization"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["app_user.id"],
            name=op.f("fk_membership_user_id_app_user"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_membership")),
        sa.UniqueConstraint("organization_id", "id", name="uq_membership_organization_id_id"),
        sa.UniqueConstraint(
            "organization_id", "user_id", name="uq_membership_organization_id_user_id"
        ),
    )
    op.create_index(
        op.f("ix_membership_organization_id"), "membership", ["organization_id"], unique=False
    )
    op.create_index(op.f("ix_membership_user_id"), "membership", ["user_id"], unique=False)
    op.create_table(
        "role",
        sa.Column("organization_id", sa.UUID(), nullable=True),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("name_uz", sa.String(length=128), nullable=False),
        sa.Column("name_ru", sa.String(length=128), nullable=False),
        sa.Column("name_en", sa.String(length=128), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_system", sa.Boolean(), nullable=False),
        sa.Column("is_read_only", sa.Boolean(), nullable=False),
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
            "(is_system AND organization_id IS NULL) OR NOT is_system",
            name=op.f("ck_role_system_roles_are_global"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.id"],
            name=op.f("fk_role_organization_id_organization"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_role")),
    )
    op.create_index(
        "uq_role_global_code",
        "role",
        ["code"],
        unique=True,
        postgresql_where=sa.text("organization_id IS NULL"),
    )
    op.create_index(
        "uq_role_organization_code",
        "role",
        ["organization_id", "code"],
        unique=True,
        postgresql_where=sa.text("organization_id IS NOT NULL"),
    )
    op.create_table(
        "membership_role",
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("membership_id", sa.UUID(), nullable=False),
        sa.Column("role_id", sa.UUID(), nullable=False),
        sa.Column("granted_by_user_id", sa.UUID(), nullable=True),
        sa.Column(
            "granted_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["granted_by_user_id"],
            ["app_user.id"],
            name=op.f("fk_membership_role_granted_by_user_id_app_user"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "membership_id"],
            ["membership.organization_id", "membership.id"],
            name="fk_membership_role_membership",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["role_id"],
            ["role.id"],
            name=op.f("fk_membership_role_role_id_role"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "organization_id", "membership_id", "role_id", name=op.f("pk_membership_role")
        ),
    )
    op.create_index("ix_membership_role_role_id", "membership_role", ["role_id"], unique=False)
    op.create_table(
        "refresh_token",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("membership_id", sa.UUID(), nullable=True),
        sa.Column(
            "issued_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_reason", sa.String(length=64), nullable=True),
        sa.Column("replaced_by_id", sa.UUID(), nullable=True),
        sa.Column("user_agent", sa.String(length=255), nullable=True),
        sa.Column("ip_address", sa.String(length=64), nullable=True),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["membership_id"],
            ["membership.id"],
            name=op.f("fk_refresh_token_membership_id_membership"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["replaced_by_id"],
            ["refresh_token.id"],
            name=op.f("fk_refresh_token_replaced_by_id_refresh_token"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["app_user.id"],
            name=op.f("fk_refresh_token_user_id_app_user"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_refresh_token")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_refresh_token_token_hash")),
    )
    op.create_index("ix_refresh_token_expires_at", "refresh_token", ["expires_at"], unique=False)
    op.create_index(op.f("ix_refresh_token_user_id"), "refresh_token", ["user_id"], unique=False)
    op.create_table(
        "role_permission",
        sa.Column("role_id", sa.UUID(), nullable=False),
        sa.Column("permission_code", sa.String(length=80), nullable=False),
        sa.ForeignKeyConstraint(
            ["permission_code"],
            ["permission.code"],
            name=op.f("fk_role_permission_permission_code_permission"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["role_id"],
            ["role.id"],
            name=op.f("fk_role_permission_role_id_role"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("role_id", "permission_code", name=op.f("pk_role_permission")),
    )
    # ### end generated schema ###

    op.execute(AUDIT_GUARD_FUNCTION)
    op.execute(AUDIT_GUARD_TRIGGER)
    _seed_reference_data()


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_audit_log_append_only ON audit_log")
    op.execute("DROP FUNCTION IF EXISTS audit_log_is_append_only()")

    # ### schema (generated from the SQLAlchemy models) ###
    op.drop_table("role_permission")
    op.drop_index(op.f("ix_refresh_token_user_id"), table_name="refresh_token")
    op.drop_index("ix_refresh_token_expires_at", table_name="refresh_token")
    op.drop_table("refresh_token")
    op.drop_index("ix_membership_role_role_id", table_name="membership_role")
    op.drop_table("membership_role")
    op.drop_index(
        "uq_role_organization_code",
        table_name="role",
        postgresql_where=sa.text("organization_id IS NOT NULL"),
    )
    op.drop_index(
        "uq_role_global_code",
        table_name="role",
        postgresql_where=sa.text("organization_id IS NULL"),
    )
    op.drop_table("role")
    op.drop_index(op.f("ix_membership_user_id"), table_name="membership")
    op.drop_index(op.f("ix_membership_organization_id"), table_name="membership")
    op.drop_table("membership")
    op.drop_index("ix_audit_log_target", table_name="audit_log")
    op.drop_index(op.f("ix_audit_log_request_id"), table_name="audit_log")
    op.drop_index("ix_audit_log_organization_id_occurred_at", table_name="audit_log")
    op.drop_index(op.f("ix_audit_log_organization_id"), table_name="audit_log")
    op.drop_index(op.f("ix_audit_log_occurred_at"), table_name="audit_log")
    op.drop_index(op.f("ix_audit_log_business_event"), table_name="audit_log")
    op.drop_index(op.f("ix_audit_log_actor_user_id"), table_name="audit_log")
    op.drop_index(op.f("ix_audit_log_action"), table_name="audit_log")
    op.drop_table("audit_log")
    op.drop_index(op.f("ix_permission_module"), table_name="permission")
    op.drop_table("permission")
    op.drop_index(op.f("ix_organization_tax_number"), table_name="organization")
    op.drop_table("organization")
    op.drop_table("currency")
    op.drop_table("app_user")
    # ### end generated schema ###
