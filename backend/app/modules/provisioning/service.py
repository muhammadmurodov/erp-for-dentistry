"""Create an organization together with its primary owner.

Why this is its own module: an organization with nobody authorised over it is
not a usable state — somebody has to be able to grant the first roles — but
making `organization` depend on `identity` (or the reverse) would introduce a
cycle. This service composes the two from above, in a single transaction.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Conflict, NotFound
from app.modules.audit import events
from app.modules.audit.service import AuditRecorder
from app.modules.identity.models import Membership, MembershipRole, Role, User
from app.modules.identity.service import IdentityService
from app.modules.organization.models import Currency, Organization


class ProvisioningService:
    def __init__(self, session: AsyncSession, audit: AuditRecorder) -> None:
        self._session = session
        self._audit = audit

    async def create_organization_with_owner(
        self,
        *,
        code: str,
        legal_name: str,
        owner_email: str,
        owner_full_name: str,
        owner_password: str,
        **attributes: Any,
    ) -> tuple[Organization, User, Membership]:
        existing = await self._session.execute(
            select(Organization).where(Organization.code == code)
        )
        if existing.scalar_one_or_none() is not None:
            raise Conflict(f"An organization with code {code!r} already exists")

        currency_code = attributes.get("base_currency", "UZS")
        if await self._session.get(Currency, currency_code) is None:
            raise NotFound(f"Currency {currency_code!r} is not configured")

        organization = Organization(code=code, legal_name=legal_name, **attributes)
        self._session.add(organization)
        await self._session.flush()
        self._audit.bind(organization_id=organization.id)
        self._audit.record(
            events.ORGANIZATION_CREATED,
            target_type="organization",
            target_id=organization.id,
            target_label=code,
            organization_id=organization.id,
            new_values={"code": code, "legal_name": legal_name},
        )

        identity = IdentityService(self._session, self._audit)
        user, membership = await identity.create_user_in_organization(
            organization_id=organization.id,
            email=owner_email,
            full_name=owner_full_name,
            password=owner_password,
            role_codes=[],
            job_title="Owner",
            must_change_password=False,
        )
        membership.is_primary_owner = True

        owner_role = await self._owner_role()
        self._session.add(
            MembershipRole(
                organization_id=organization.id,
                membership_id=membership.id,
                role_id=owner_role.id,
            )
        )
        await self._session.flush()
        self._audit.record(
            events.ROLE_GRANTED,
            target_type="membership",
            target_id=membership.id,
            target_label=user.email,
            organization_id=organization.id,
            new_values={"role_codes": ["owner"], "added": ["owner"]},
        )
        return organization, user, membership

    async def _owner_role(self) -> Role:
        role = (
            await self._session.execute(
                select(Role).where(Role.code == "owner", Role.organization_id.is_(None))
            )
        ).scalar_one_or_none()
        if role is None:  # pragma: no cover - seeded by the Phase 1 migration
            raise NotFound("The system 'owner' role is missing; run the database migrations")
        return role


__all__ = ["ProvisioningService", "uuid"]
