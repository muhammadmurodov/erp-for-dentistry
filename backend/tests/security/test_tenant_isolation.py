"""One organization must never reach another's records.

These tests are the reason `Membership` carries authority rather than `User`,
and the reason organization-scoped tables carry a composite foreign key. Both
the application path and the database path are checked.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import PermissionDenied
from app.modules.audit.service import AuditRecorder
from app.modules.identity.service import IdentityService

pytestmark = pytest.mark.security

OWNER_PASSWORD = "fixture-owner-password-1"
STAFF_PASSWORD = "fixture-staff-password-1"


class TestApiIsolation:
    async def test_each_owner_sees_only_their_own_organization(
        self, client: AsyncClient, login, org_a, org_b
    ) -> None:
        headers_a = await login(org_a.owner.email, OWNER_PASSWORD)
        headers_b = await login(org_b.owner.email, OWNER_PASSWORD)

        body_a = (await client.get("/api/v1/organization", headers=headers_a)).json()
        body_b = (await client.get("/api/v1/organization", headers=headers_b)).json()

        assert body_a["id"] == str(org_a.id)
        assert body_b["id"] == str(org_b.id)
        assert body_a["id"] != body_b["id"]

    async def test_user_list_is_scoped_to_the_callers_organization(
        self, client: AsyncClient, login, org_a, org_b
    ) -> None:
        headers = await login(org_a.owner.email, OWNER_PASSWORD)
        response = await client.get("/api/v1/users", headers=headers)
        assert response.status_code == 200
        emails = {row["email"] for row in response.json()}
        assert org_a.owner.email in emails
        assert org_b.owner.email not in emails
        for email in emails:
            assert email.endswith(f"{org_a.organization.code}.example.com")

    async def test_foreign_membership_id_does_not_resolve(
        self, client: AsyncClient, login, org_a, org_b
    ) -> None:
        """A valid id from another tenant must behave exactly like a wrong one."""
        headers = await login(org_a.owner.email, OWNER_PASSWORD)
        foreign_membership_id = org_b.owner_membership.id

        response = await client.put(
            f"/api/v1/users/{foreign_membership_id}/roles",
            headers=headers,
            json={"role_codes": ["cashier"]},
        )
        assert response.status_code == 404

        nonexistent = await client.put(
            f"/api/v1/users/{uuid.uuid4()}/roles",
            headers=headers,
            json={"role_codes": ["cashier"]},
        )
        # Indistinguishable: the id's existence elsewhere is not disclosed.
        assert nonexistent.status_code == response.status_code
        assert nonexistent.json()["code"] == response.json()["code"]

    async def test_foreign_membership_cannot_be_deactivated(
        self, client: AsyncClient, login, org_a, org_b
    ) -> None:
        # Read the attributes before the failing request: its rollback expires
        # every ORM object in the shared test session.
        owner_b_email = org_b.owner.email
        foreign_membership_id = org_b.owner_membership.id
        headers = await login(org_a.owner.email, OWNER_PASSWORD)

        response = await client.post(
            f"/api/v1/users/{foreign_membership_id}/deactivate", headers=headers
        )
        assert response.status_code == 404

        # And org B's owner still has working access.
        headers_b = await login(owner_b_email, OWNER_PASSWORD)
        assert (await client.get("/api/v1/organization", headers=headers_b)).status_code == 200

    async def test_audit_trail_is_scoped(self, client: AsyncClient, login, org_a, org_b) -> None:
        headers = await login(org_a.owner.email, OWNER_PASSWORD)
        response = await client.get("/api/v1/audit/logs", headers=headers)
        assert response.status_code == 200
        for row in response.json()["items"]:
            assert str(org_b.id) not in str(row)

    async def test_patch_cannot_target_another_organization(
        self, client: AsyncClient, login, org_a, org_b
    ) -> None:
        """The organization is taken from the token; there is no id to override."""
        headers = await login(org_a.owner.email, OWNER_PASSWORD)
        response = await client.patch(
            "/api/v1/organization",
            headers=headers,
            json={"short_name": "Renamed by A", "id": str(org_b.id)},
        )
        assert response.status_code == 200
        assert response.json()["id"] == str(org_a.id)
        assert response.json()["short_name"] == "Renamed by A"

        headers_b = await login(org_b.owner.email, OWNER_PASSWORD)
        body_b = (await client.get("/api/v1/organization", headers=headers_b)).json()
        assert body_b["short_name"] != "Renamed by A"


class TestServiceIsolation:
    async def test_membership_lookup_is_organization_specific(
        self, db: AsyncSession, audit: AuditRecorder, org_a, org_b
    ) -> None:
        service = IdentityService(db, audit)
        found = await service.get_membership(user_id=org_a.owner.id, organization_id=org_b.id)
        assert found is None

    async def test_signing_in_to_a_foreign_organization_is_refused(
        self, db: AsyncSession, audit: AuditRecorder, org_a, org_b
    ) -> None:
        service = IdentityService(db, audit)
        with pytest.raises(PermissionDenied):
            await service.authenticate(
                email=org_a.owner.email,
                password=OWNER_PASSWORD,
                organization_id=org_b.id,
            )

    async def test_permissions_are_per_membership_not_per_user(
        self, db: AsyncSession, audit: AuditRecorder, org_a, org_b
    ) -> None:
        """The same person may be an owner here and an auditor there."""
        service = IdentityService(db, audit)
        _, membership_b = await service.create_user_in_organization(
            organization_id=org_b.id,
            email=org_a.owner.email,
            full_name="Shared Person",
            password=STAFF_PASSWORD,
            role_codes=["auditor"],
            must_change_password=False,
        )
        await db.flush()

        owner_perms = await service.resolve_permissions(org_a.owner_membership)
        auditor_perms = await service.resolve_permissions(membership_b)

        assert "accounting.journal.post" in owner_perms
        assert "accounting.journal.post" not in auditor_perms
        assert "organization.manage" not in auditor_perms


class TestDatabaseLevelIsolation:
    async def test_composite_foreign_key_rejects_a_cross_tenant_grant(
        self, db: AsyncSession, org_a, org_b
    ) -> None:
        """The database itself refuses the row, not just the application.

        `membership_role` references ``membership (organization_id, id)``, so
        claiming org B's id while pointing at org A's membership cannot be
        stored however the insert is phrased.
        """
        role_id = (
            await db.execute(
                text("SELECT id FROM role WHERE code = 'owner' AND organization_id IS NULL")
            )
        ).scalar_one()

        await db.begin_nested()
        with pytest.raises(IntegrityError):
            await db.execute(
                text(
                    """
                    INSERT INTO membership_role
                        (organization_id, membership_id, role_id)
                    VALUES (:org_b, :membership_a, :role)
                    """
                ),
                {
                    "org_b": org_b.id,
                    "membership_a": org_a.owner_membership.id,
                    "role": role_id,
                },
            )
            await db.flush()
        await db.rollback()

    async def test_unique_organization_id_pair_exists_on_scoped_tables(
        self, db: AsyncSession
    ) -> None:
        """The constraint that makes composite foreign keys possible."""
        rows = (
            (
                await db.execute(
                    text(
                        """
                    SELECT conname FROM pg_constraint
                    WHERE conrelid = 'membership'::regclass
                      AND contype = 'u'
                    """
                    )
                )
            )
            .scalars()
            .all()
        )
        assert "uq_membership_organization_id_id" in rows
