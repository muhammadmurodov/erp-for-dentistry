"""The authenticated caller, as the rest of the application sees it."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from app.core.errors import CrossOrganizationAccess, PermissionDenied


@dataclass(frozen=True, slots=True)
class RequestContext:
    """Resolved identity and authority for one request.

    `organization_id` is the only organization this request may touch. Services
    take it from here and never from a request body or path parameter, which is
    what stops an attacker from naming someone else's organization.
    """

    user_id: uuid.UUID
    email: str
    full_name: str
    permissions: frozenset[str]
    organization_id: uuid.UUID | None = None
    membership_id: uuid.UUID | None = None
    is_superuser: bool = False
    request_id: str | None = None
    ip_address: str | None = None
    user_agent: str | None = None

    def has(self, permission: str) -> bool:
        return permission in self.permissions

    def has_any(self, *permissions: str) -> bool:
        return any(p in self.permissions for p in permissions)

    def require(self, *permissions: str) -> None:
        """Raise unless the caller holds **every** named permission.

        Being a superuser is not a substitute: platform administration and
        organization authority are separate, so a superuser without a
        membership still cannot post to an organization's ledger.
        """
        missing = [p for p in permissions if p not in self.permissions]
        if missing:
            raise PermissionDenied(
                "You do not have permission to perform this action",
                details={"missing_permissions": sorted(missing)},
            )

    def require_organization(self) -> uuid.UUID:
        if self.organization_id is None:
            raise PermissionDenied("Select an organization before using this feature")
        return self.organization_id

    def assert_owns(self, record_organization_id: uuid.UUID) -> None:
        """Last line of defence for a record fetched without a scope filter.

        Prefer `app.core.tenancy.scoped` so the row is never read at all; use
        this when a primary-key lookup was unavoidable.
        """
        if record_organization_id != self.organization_id:
            raise CrossOrganizationAccess("You do not have permission to perform this action")
