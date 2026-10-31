"""Organization scoping.

Every business record belongs to exactly one organization. Two mechanisms keep
that true, and they are deliberately redundant:

1. `OrganizationScopedMixin` adds the `organization_id` column and a
   ``UNIQUE (organization_id, id)`` constraint. That constraint is what lets a
   child table carry a *composite* foreign key — ``(organization_id, parent_id)
   -> parent (organization_id, id)`` — so the database itself refuses a journal
   line whose entry belongs to a different organization. No application bug can
   produce that row.
2. `scoped` filters every query by the organization resolved from the caller's
   token, so a record from another tenant is never even read.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any, TypeVar

from sqlalchemy import ForeignKey, Select, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, declared_attr, mapped_column

if TYPE_CHECKING:  # pragma: no cover
    pass

ModelT = TypeVar("ModelT")


class OrganizationScopedMixin:
    """Adds the tenant column plus the uniqueness a composite FK needs."""

    @declared_attr
    def organization_id(cls) -> Mapped[uuid.UUID]:
        return mapped_column(
            PgUUID(as_uuid=True),
            ForeignKey("organization.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        )

    @declared_attr.directive
    def __table_args__(cls) -> tuple[Any, ...]:
        # Subclasses that need more arguments override this and call
        # `organization_scope_args()` themselves.
        return organization_scope_args(cls.__tablename__)  # type: ignore[attr-defined]


def organization_scope_args(table_name: str) -> tuple[Any, ...]:
    """The table arguments every organization-scoped table must carry."""
    return (
        UniqueConstraint(
            "organization_id",
            "id",
            name=f"uq_{table_name}_organization_id_id",
        ),
    )


def scoped(stmt: Select[Any], model: type, organization_id: uuid.UUID) -> Select[Any]:
    """Narrow `stmt` to one organization.

    Call this on *every* read of an organization-scoped table. The companion
    test suite asserts that each scoped router honours it.
    """
    return stmt.where(model.organization_id == organization_id)
