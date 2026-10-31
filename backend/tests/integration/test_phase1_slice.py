"""The Phase 1 vertical slice, end to end through HTTP.

Provision a tenant, sign in, read the organization, add a member, grant roles,
change a password, and confirm the audit trail recorded each step. If this
test passes, the foundation is demonstrable with fictional data — which is the
project's own definition of done for Phase 1.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit import events
from app.modules.audit.models import AuditLog

pytestmark = pytest.mark.integration

OWNER_PASSWORD = "fixture-owner-password-1"
STAFF_PASSWORD = "fixture-staff-password-1"
NEW_USER_PASSWORD = "a-new-staff-password-42"


class TestOnboardingToOperating:
    async def test_full_flow(self, client: AsyncClient, org_a, db: AsyncSession) -> None:
        # 1. Sign in as the owner the provisioning step created.
        login = await client.post(
            "/api/v1/auth/login",
            json={"email": org_a.owner.email, "password": OWNER_PASSWORD},
        )
        assert login.status_code == 200, login.text
        tokens = login.json()
        headers = {"Authorization": f"Bearer {tokens['access_token']}"}

        # 2. The session knows who and where it is.
        me = await client.get("/api/v1/auth/me", headers=headers)
        assert me.status_code == 200
        body = me.json()
        assert body["email"] == org_a.owner.email
        assert body["active_membership"]["organization_id"] == str(org_a.id)
        assert body["active_membership"]["is_primary_owner"] is True
        assert body["active_membership"]["role_codes"] == ["owner"]
        # An owner holds every permission in the catalogue.
        from app.core.permissions import ALL_PERMISSION_CODES

        assert set(body["permissions"]) == ALL_PERMISSION_CODES

        # 3. Read and edit the organization profile.
        organization = await client.get("/api/v1/organization", headers=headers)
        assert organization.status_code == 200
        assert organization.json()["base_currency"] == "UZS"
        assert organization.json()["timezone"] == "Asia/Tashkent"

        updated = await client.patch(
            "/api/v1/organization",
            headers=headers,
            json={
                "short_name": "Integration Clinic",
                "tax_number": "123456789",
                "activity_description": "Stomatologiya xizmatlari",
            },
        )
        assert updated.status_code == 200
        assert updated.json()["short_name"] == "Integration Clinic"
        assert updated.json()["tax_number"] == "123456789"

        # 4. Reference data is available.
        currencies = await client.get("/api/v1/organization/currencies", headers=headers)
        assert currencies.status_code == 200
        uzs = next(c for c in currencies.json() if c["code"] == "UZS")
        assert uzs["decimal_places"] == 2

        # 5. Add a member with a role.
        created = await client.post(
            "/api/v1/users",
            headers=headers,
            json={
                "email": "new.cashier@integration.example.com",
                "full_name": "New Cashier",
                "password": NEW_USER_PASSWORD,
                "locale": "uz",
                "job_title": "Kassir",
                "role_codes": ["cashier"],
            },
        )
        assert created.status_code == 201, created.text
        new_member = created.json()
        assert new_member["role_codes"] == ["cashier"]
        assert new_member["membership_active"] is True

        # 6. The new member appears in the list, scoped to this organization.
        listing = await client.get("/api/v1/users", headers=headers)
        assert listing.status_code == 200
        assert "new.cashier@integration.example.com" in {row["email"] for row in listing.json()}

        # 7. The new member can sign in and is limited to their own authority.
        member_login = await client.post(
            "/api/v1/auth/login",
            json={
                "email": "new.cashier@integration.example.com",
                "password": NEW_USER_PASSWORD,
            },
        )
        assert member_login.status_code == 200
        assert member_login.json()["must_change_password"] is True
        member_headers = {"Authorization": f"Bearer {member_login.json()['access_token']}"}
        assert (await client.get("/api/v1/organization", headers=member_headers)).status_code == 200
        assert (
            await client.patch(
                "/api/v1/organization",
                headers=member_headers,
                json={"short_name": "Not allowed"},
            )
        ).status_code == 403

        # 8. The member changes their password; the old one stops working.
        changed = await client.post(
            "/api/v1/auth/change-password",
            headers=member_headers,
            json={
                "current_password": NEW_USER_PASSWORD,
                "new_password": "an-even-better-password-7",
            },
        )
        assert changed.status_code == 204
        stale = await client.post(
            "/api/v1/auth/login",
            json={
                "email": "new.cashier@integration.example.com",
                "password": NEW_USER_PASSWORD,
            },
        )
        assert stale.status_code == 401
        fresh = await client.post(
            "/api/v1/auth/login",
            json={
                "email": "new.cashier@integration.example.com",
                "password": "an-even-better-password-7",
            },
        )
        assert fresh.status_code == 200
        assert fresh.json()["must_change_password"] is False

        # 9. Roles can be changed by the owner.
        regranted = await client.put(
            f"/api/v1/users/{new_member['membership_id']}/roles",
            headers=headers,
            json={"role_codes": ["accountant", "auditor"]},
        )
        assert regranted.status_code == 200
        assert regranted.json()["role_codes"] == ["accountant", "auditor"]

        # 10. Every step above is in the audit trail.
        organization_actions = set(
            (await db.execute(select(AuditLog.action).where(AuditLog.organization_id == org_a.id)))
            .scalars()
            .all()
        )
        for required in (
            events.ORGANIZATION_CREATED,
            events.ORGANIZATION_UPDATED,
            events.USER_CREATED,
            events.MEMBERSHIP_CREATED,
            events.ROLE_GRANTED,
            events.LOGIN_SUCCEEDED,
            events.PASSWORD_CHANGED,
        ):
            assert required in organization_actions, f"missing audit action {required}"

        # A rejected sign-in is recorded without an organization: authentication
        # fails before any membership is resolved, so attributing it to a tenant
        # would be a guess.
        failed = (
            (await db.execute(select(AuditLog).where(AuditLog.action == events.LOGIN_FAILED)))
            .scalars()
            .all()
        )
        assert failed, "the stale-password sign-in must be audited"
        assert any(row.actor_label == "new.cashier@integration.example.com" for row in failed)
        assert all(row.organization_id is None for row in failed)

    async def test_organization_update_is_audited_with_old_and_new(
        self, client: AsyncClient, login, org_a, db: AsyncSession
    ) -> None:
        headers = await login(org_a.owner.email, OWNER_PASSWORD)
        await client.patch("/api/v1/organization", headers=headers, json={"phone": "+998000000000"})
        row = (
            await db.execute(
                select(AuditLog)
                .where(
                    AuditLog.action == events.ORGANIZATION_UPDATED,
                    AuditLog.organization_id == org_a.id,
                )
                .order_by(AuditLog.occurred_at.desc())
                .limit(1)
            )
        ).scalar_one()
        assert row.new_values["phone"] == "+998000000000"
        assert "phone" in row.old_values
        assert row.request_id


class TestHealthAndPlatform:
    async def test_liveness_needs_no_authentication(self, client: AsyncClient) -> None:
        response = await client.get("/health/live")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    async def test_readiness_checks_the_database(self, client: AsyncClient) -> None:
        response = await client.get("/health/ready")
        assert response.status_code == 200
        assert response.json()["checks"]["database"] == "ok"

    async def test_module_registry_reports_planned_modules_honestly(
        self, client: AsyncClient, login, org_a
    ) -> None:
        """A module that is not built must say so, not look finished."""
        headers = await login(org_a.owner.email, OWNER_PASSWORD)
        response = await client.get("/api/v1/platform/modules", headers=headers)
        assert response.status_code == 200
        by_key = {m["key"]: m for m in response.json()}

        # Phase 1 delivered these partially.
        assert by_key["settings"]["status"] == "partial"
        assert by_key["settings"]["available_features"]
        assert by_key["settings"]["missing_features"]

        # Nothing beyond Phase 1 may claim to be available.
        for key, info in by_key.items():
            if info["phase"] > 1:
                assert info["status"] != "available", (
                    f"{key} is phase {info['phase']} and cannot be available yet"
                )
                assert info["missing_features"], f"{key} must list what is missing"

    async def test_permission_catalogue_is_exposed_for_the_role_editor(
        self, client: AsyncClient, login, org_a
    ) -> None:
        headers = await login(org_a.owner.email, OWNER_PASSWORD)
        response = await client.get("/api/v1/platform/permissions", headers=headers)
        assert response.status_code == 200
        from app.core.permissions import ALL_PERMISSION_CODES

        assert {p["code"] for p in response.json()} == ALL_PERMISSION_CODES


class TestResponseHygiene:
    async def test_every_response_carries_a_request_id(self, client: AsyncClient) -> None:
        response = await client.get("/health/live")
        assert response.headers["X-Request-ID"]

    async def test_security_headers_are_present(self, client: AsyncClient) -> None:
        response = await client.get("/health/live")
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert response.headers["X-Frame-Options"] == "DENY"
        assert response.headers["Cache-Control"] == "no-store"

    async def test_domain_errors_use_a_uniform_shape(self, client: AsyncClient) -> None:
        response = await client.get("/api/v1/organization")
        body = response.json()
        assert set(body) >= {"code", "message"}
        assert isinstance(body["code"], str)
