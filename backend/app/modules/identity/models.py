"""Users, roles, memberships and sessions.

A `User` is a person and is global: the same accountant can serve two
organizations. Authority is never attached to the user — it is attached to a
`Membership`, which is the pair (user, organization). That is what makes
"accountant in clinic A, auditor in clinic B" expressible, and what makes a
cross-organization privilege impossible to express at all.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, TimestampMixin, UUIDPrimaryKeyMixin


class User(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "app_user"

    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(32))
    locale: Mapped[str] = mapped_column(String(8), nullable=False, default="uz")

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    #: Platform operator. Carries no organization authority on its own: a
    #: superuser still needs a membership to touch an organization's books,
    #: so "escalate to read another tenant" is not a reachable state.
    is_superuser: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    must_change_password: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failed_login_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # `raise_on_sql`: an implicit lazy load in async SQLAlchemy fails with
    # an unrelated greenlet error, so every caller must load explicitly.
    memberships: Mapped[list[Membership]] = relationship(back_populates="user", lazy="raise_on_sql")

    __table_args__ = (
        CheckConstraint("email = lower(email)", name="email_lowercase"),
        CheckConstraint("position('@' in email) > 1", name="email_has_at_sign"),
        CheckConstraint("char_length(full_name) >= 2", name="full_name_min_length"),
    )


class Permission(Base):
    """Mirror of `app.core.permissions.PERMISSIONS`, seeded by migration.

    The table exists so that role grants can be enforced by a foreign key
    rather than by a string the application hopes is spelled correctly.
    """

    __tablename__ = "permission"

    code: Mapped[str] = mapped_column(String(80), primary_key=True)
    module: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    is_sensitive: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class Role(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A named bundle of permissions.

    `organization_id IS NULL` marks a system role shared by every tenant.
    A tenant may also define its own roles; the unique index below lets the
    same code exist once per organization and once globally.
    """

    __tablename__ = "role"

    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("organization.id", ondelete="CASCADE")
    )
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name_uz: Mapped[str] = mapped_column(String(128), nullable=False)
    name_ru: Mapped[str] = mapped_column(String(128), nullable=False)
    name_en: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    #: A read-only role may hold no write permission; validated on every edit.
    is_read_only: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    permissions: Mapped[list[RolePermission]] = relationship(
        back_populates="role", cascade="all, delete-orphan", lazy="raise_on_sql"
    )

    # Partial unique indexes: a role code is unique among the global system
    # roles, and unique again within each organization that defines its own.
    __table_args__ = (
        Index(
            "uq_role_global_code",
            "code",
            unique=True,
            postgresql_where=text("organization_id IS NULL"),
        ),
        Index(
            "uq_role_organization_code",
            "organization_id",
            "code",
            unique=True,
            postgresql_where=text("organization_id IS NOT NULL"),
        ),
        CheckConstraint(
            "(is_system AND organization_id IS NULL) OR NOT is_system",
            name="system_roles_are_global",
        ),
    )


class RolePermission(Base):
    __tablename__ = "role_permission"

    role_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("role.id", ondelete="CASCADE"), primary_key=True
    )
    permission_code: Mapped[str] = mapped_column(
        String(80),
        ForeignKey("permission.code", ondelete="RESTRICT"),
        primary_key=True,
    )

    role: Mapped[Role] = relationship(back_populates="permissions")


class Membership(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A user's standing inside one organization. Authority lives here."""

    __tablename__ = "membership"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("organization.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("app_user.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    #: Free-text job title shown in the UI; not an authorisation input.
    job_title: Mapped[str | None] = mapped_column(String(128))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    #: Set on the membership created with the organization. Protected: the
    #: last active owner membership cannot be removed or demoted.
    is_primary_owner: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    user: Mapped[User] = relationship(back_populates="memberships", lazy="raise_on_sql")
    roles: Mapped[list[MembershipRole]] = relationship(
        back_populates="membership",
        cascade="all, delete-orphan",
        lazy="raise_on_sql",
    )

    __table_args__ = (
        UniqueConstraint(
            "organization_id", "user_id", name="uq_membership_organization_id_user_id"
        ),
        UniqueConstraint("organization_id", "id", name="uq_membership_organization_id_id"),
    )


class MembershipRole(Base):
    """Grant of a role to a membership.

    The composite foreign key on ``(organization_id, membership_id)`` is the
    point of this table's shape: it makes granting an organization's role to
    another organization's membership unrepresentable in the database.
    """

    __tablename__ = "membership_role"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), nullable=False, primary_key=True
    )
    membership_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), nullable=False, primary_key=True
    )
    role_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("role.id", ondelete="RESTRICT"),
        nullable=False,
        primary_key=True,
    )
    granted_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("app_user.id", ondelete="SET NULL")
    )
    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    membership: Mapped[Membership] = relationship(back_populates="roles")

    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "membership_id"],
            ["membership.organization_id", "membership.id"],
            ondelete="CASCADE",
            name="fk_membership_role_membership",
        ),
        Index("ix_membership_role_role_id", "role_id"),
    )


class RefreshToken(Base, UUIDPrimaryKeyMixin):
    """A refresh session. Only the SHA-256 digest of the token is stored."""

    __tablename__ = "refresh_token"

    user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("app_user.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    #: The membership this session is bound to, if the user selected one.
    membership_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("membership.id", ondelete="CASCADE")
    )
    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_reason: Mapped[str | None] = mapped_column(String(64))
    #: Set when this token was consumed and replaced, so that replaying an
    #: already-rotated refresh token is detectable.
    replaced_by_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("refresh_token.id", ondelete="SET NULL")
    )
    user_agent: Mapped[str | None] = mapped_column(String(255))
    ip_address: Mapped[str | None] = mapped_column(String(64))

    __table_args__ = (Index("ix_refresh_token_expires_at", "expires_at"),)


__all__ = [
    "Membership",
    "MembershipRole",
    "Permission",
    "RefreshToken",
    "Role",
    "RolePermission",
    "User",
]
