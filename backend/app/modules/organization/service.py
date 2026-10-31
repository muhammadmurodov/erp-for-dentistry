"""Organization profile reads and edits.

Deliberately knows nothing about users or roles: creating a tenant together
with its first owner spans two modules and lives in
`app.modules.provisioning` instead, which is what keeps this module free of a
dependency on identity.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFound
from app.modules.audit import events
from app.modules.audit.service import AuditRecorder, changed_fields
from app.modules.organization.models import Currency, Organization

#: Fields an organization administrator may change. `code` and
#: `base_currency` are excluded: both are referenced by posted accounting
#: records, so changing one would silently reinterpret history.
EDITABLE_FIELDS = (
    "legal_name",
    "short_name",
    "tax_number",
    "legal_form",
    "activity_description",
    "address",
    "phone",
    "email",
    "registered_on",
    "timezone",
    "default_locale",
)


class OrganizationService:
    def __init__(self, session: AsyncSession, audit: AuditRecorder) -> None:
        self._session = session
        self._audit = audit

    async def get(self, organization_id: uuid.UUID) -> Organization:
        organization = await self._session.get(Organization, organization_id)
        if organization is None:
            raise NotFound("Organization not found")
        return organization

    async def get_by_code(self, code: str) -> Organization | None:
        stmt = select(Organization).where(Organization.code == code)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def list_currencies(self) -> list[Currency]:
        stmt = select(Currency).where(Currency.is_active.is_(True)).order_by(Currency.code)
        return list((await self._session.execute(stmt)).scalars().all())

    async def update(self, *, organization_id: uuid.UUID, changes: dict[str, Any]) -> Organization:
        organization = await self.get(organization_id)
        before = {f: getattr(organization, f) for f in EDITABLE_FIELDS}
        for field, value in changes.items():
            if field in EDITABLE_FIELDS and value is not None:
                setattr(organization, field, value)
        after = {f: getattr(organization, f) for f in EDITABLE_FIELDS}
        old, new = changed_fields(before, after)
        if new:
            self._audit.record(
                events.ORGANIZATION_UPDATED,
                target_type="organization",
                target_id=organization.id,
                target_label=organization.code,
                old_values=old,
                new_values=new,
                organization_id=organization.id,
            )
        return organization
