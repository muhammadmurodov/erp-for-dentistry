"""Backend authorization is the boundary; the frontend only mirrors it.

Every test here calls the API directly, bypassing any UI, because that is how
an attacker would.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import SYSTEM_ROLES_BY_CODE
from app.modules.audit import events
from app.modules.audit.models import AuditLog
from app.modules.identity.service import IdentityService

pytestmark = pytest.mark.security

OWNER_PASSWORD = "fixture-owner-password-1"
STAFF_PASSWORD = "fixture-staff-password-1"


class TestLeastPrivilege:
    async def test_cashier_cannot_edit_the_organization(
        self, client: AsyncClient, login, org_a
    ) -> None:
        headers = await login(f"cashier@{org_a.organization.code}.example.com", STAFF_PASSWORD)
        response = await client.patch(
            "/api/v1/organization", headers=headers, json={"short_name": "Hijacked"}
        )
        assert response.status_code == 403
        assert response.json()["code"] == "permission_denied"

    async def test_cashier_cannot_list_users(self, client: AsyncClient, login, org_a) -> None:
        headers = await login(f"cashier@{org_a.organization.code}.example.com", STAFF_PASSWORD)
        assert (await client.get("/api/v1/users", headers=headers)).status_code == 403

    async def test_cashier_cannot_read_the_audit_trail(
        self, client: AsyncClient, login, org_a
    ) -> None:
        headers = await login(f"cashier@{org_a.organization.code}.example.com", STAFF_PASSWORD)
        assert (await client.get("/api/v1/audit/logs", headers=headers)).status_code == 403

    async def test_auditor_can_read_but_not_write(self, client: AsyncClient, login, org_a) -> None:
        headers = await login(f"auditor@{org_a.organization.code}.example.com", STAFF_PASSWORD)
        assert (await client.get("/api/v1/organization", headers=headers)).status_code == 200
        assert (await client.get("/api/v1/audit/logs", headers=headers)).status_code == 200
        assert (
            await client.patch("/api/v1/organization", headers=headers, json={"short_name": "No"})
        ).status_code == 403

    async def test_receptionist_cannot_grant_roles(self, client: AsyncClient, login, org_a) -> None:
        headers = await login(f"receptionist@{org_a.organization.code}.example.com", STAFF_PASSWORD)
        _, membership = org_a.members["cashier"]
        response = await client.put(
            f"/api/v1/users/{membership.id}/roles",
            headers=headers,
            json={"role_codes": ["owner"]},
        )
        assert response.status_code == 403

    async def test_owner_can_do_what_the_others_cannot(
        self, client: AsyncClient, login, org_a
    ) -> None:
        headers = await login(org_a.owner.email, OWNER_PASSWORD)
        assert (
            await client.patch(
                "/api/v1/organization", headers=headers, json={"short_name": "Allowed"}
            )
        ).status_code == 200
        assert (await client.get("/api/v1/users", headers=headers)).status_code == 200
        assert (await client.get("/api/v1/audit/logs", headers=headers)).status_code == 200


class TestPrivilegeEscalation:
    async def test_user_cannot_grant_themselves_a_role(
        self, client: AsyncClient, login, org_a
    ) -> None:
        """The decisive case: no self-service promotion."""
        _, membership = org_a.members["cashier"]
        headers = await login(f"cashier@{org_a.organization.code}.example.com", STAFF_PASSWORD)
        response = await client.put(
            f"/api/v1/users/{membership.id}/roles",
            headers=headers,
            json={"role_codes": ["owner"]},
        )
        assert response.status_code == 403

    async def test_forged_permission_claim_is_rejected(self, client: AsyncClient, org_a) -> None:
        """Permissions ride in a signed token; an unsigned one is worthless."""
        import base64
        import json

        payload = base64.urlsafe_b64encode(
            json.dumps(
                {
                    "sub": str(org_a.owner.id),
                    "typ": "access",
                    "org": str(org_a.id),
                    "prm": ["organization.manage"],
                    "exp": 9_999_999_999,
                }
            ).encode()
        ).decode()
        token = f"eyJhbGciOiJub25lIn0.{payload}."
        response = await client.patch(
            "/api/v1/organization",
            headers={"Authorization": f"Bearer {token}"},
            json={"short_name": "Forged"},
        )
        assert response.status_code == 401

    async def test_deactivated_membership_loses_access_on_next_sign_in(
        self, client: AsyncClient, login, org_a, db: AsyncSession
    ) -> None:
        cashier_email = f"cashier@{org_a.organization.code}.example.com"
        _, membership = org_a.members["cashier"]
        owner_headers = await login(org_a.owner.email, OWNER_PASSWORD)

        assert (
            await client.post(f"/api/v1/users/{membership.id}/deactivate", headers=owner_headers)
        ).status_code == 200

        response = await client.post(
            "/api/v1/auth/login",
            json={"email": cashier_email, "password": STAFF_PASSWORD},
        )
        # No active membership left, so there is no organization to act in.
        assert response.status_code == 403

    async def test_primary_owner_cannot_be_deactivated(
        self, client: AsyncClient, login, org_a
    ) -> None:
        """Leaving an organization with nobody authorised is not a valid state."""
        headers = await login(org_a.owner.email, OWNER_PASSWORD)
        response = await client.post(
            f"/api/v1/users/{org_a.owner_membership.id}/deactivate", headers=headers
        )
        assert response.status_code == 409
        assert "primary owner" in response.json()["message"].lower()

    async def test_primary_owner_cannot_lose_the_owner_role(
        self, client: AsyncClient, login, org_a
    ) -> None:
        headers = await login(org_a.owner.email, OWNER_PASSWORD)
        response = await client.put(
            f"/api/v1/users/{org_a.owner_membership.id}/roles",
            headers=headers,
            json={"role_codes": ["cashier"]},
        )
        assert response.status_code == 409


class TestSegregationOfDutiesEnforcement:
    async def test_conflicting_role_combination_is_refused(
        self, client: AsyncClient, login, org_a, db: AsyncSession
    ) -> None:
        """Each role is valid alone; together they break a control.

        `inventory_manager` raises purchase orders, `director` approves them.
        One person holding both defeats the approval, so the grant is refused.
        """
        assert "purchasing.order.manage" in SYSTEM_ROLES_BY_CODE["inventory_manager"].permissions
        assert "purchasing.order.approve" in SYSTEM_ROLES_BY_CODE["director"].permissions

        _, membership = org_a.members["cashier"]
        headers = await login(org_a.owner.email, OWNER_PASSWORD)
        response = await client.put(
            f"/api/v1/users/{membership.id}/roles",
            headers=headers,
            json={"role_codes": ["inventory_manager", "director"]},
        )
        assert response.status_code == 422
        body = response.json()
        assert "segregation-of-duties" in body["message"]
        assert body["details"]["problems"]

    async def test_each_of_those_roles_is_grantable_on_its_own(
        self, client: AsyncClient, login, org_a
    ) -> None:
        _, membership = org_a.members["cashier"]
        headers = await login(org_a.owner.email, OWNER_PASSWORD)
        for role in ("inventory_manager", "director"):
            response = await client.put(
                f"/api/v1/users/{membership.id}/roles",
                headers=headers,
                json={"role_codes": [role]},
            )
            assert response.status_code == 200, f"{role}: {response.text}"
            assert response.json()["role_codes"] == [role]

    async def test_unknown_role_is_refused(self, client: AsyncClient, login, org_a) -> None:
        _, membership = org_a.members["cashier"]
        headers = await login(org_a.owner.email, OWNER_PASSWORD)
        response = await client.put(
            f"/api/v1/users/{membership.id}/roles",
            headers=headers,
            json={"role_codes": ["supreme_overlord"]},
        )
        assert response.status_code == 422


class TestDenialIsAudited:
    async def test_denied_request_is_recorded(
        self, committed_client: AsyncClient, committed_org
    ) -> None:
        """A denial must leave a trace even though the request failed.

        Uses committed fixtures: the denial audit record is written in its own
        transaction so that it survives the request's rollback, and a separate
        transaction cannot see uncommitted rows.
        """
        from app.core.db import get_session_factory

        code = committed_org.organization.code
        login = await committed_client.post(
            "/api/v1/auth/login",
            json={
                "email": f"cashier@{code}.example.com",
                "password": STAFF_PASSWORD,
            },
        )
        assert login.status_code == 200, login.text
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

        denied = await committed_client.patch(
            "/api/v1/organization", headers=headers, json={"short_name": "Nope"}
        )
        assert denied.status_code == 403

        async with get_session_factory()() as fresh:
            rows = (
                (
                    await fresh.execute(
                        select(AuditLog)
                        .where(
                            AuditLog.action == events.PERMISSION_DENIED,
                            AuditLog.organization_id == committed_org.id,
                        )
                        .order_by(AuditLog.occurred_at.desc())
                    )
                )
                .scalars()
                .all()
            )
        assert rows, "a denied request must be audited"
        assert rows[0].outcome == "denied"
        assert "organization.manage" in str(rows[0].new_values)
        assert rows[0].target_label == "PATCH /api/v1/organization"


class TestRoleGrantsAreAudited:
    async def test_granting_a_role_is_recorded_with_before_and_after(
        self, client: AsyncClient, login, org_a, db: AsyncSession
    ) -> None:
        _, membership = org_a.members["cashier"]
        headers = await login(org_a.owner.email, OWNER_PASSWORD)
        await client.put(
            f"/api/v1/users/{membership.id}/roles",
            headers=headers,
            json={"role_codes": ["accountant"]},
        )
        rows = (
            (
                await db.execute(
                    select(AuditLog).where(
                        AuditLog.action.in_([events.ROLE_GRANTED, events.ROLE_REVOKED]),
                        AuditLog.target_id == str(membership.id),
                    )
                )
            )
            .scalars()
            .all()
        )
        assert rows
        granted = [r for r in rows if r.action == events.ROLE_GRANTED]
        revoked = [r for r in rows if r.action == events.ROLE_REVOKED]
        assert any("accountant" in str(r.new_values) for r in granted)
        assert any("cashier" in str(r.new_values.get("removed", [])) for r in revoked)


class TestPermissionsEndpointReflectsBackend:
    async def test_me_reports_exactly_the_granted_permissions(
        self, client: AsyncClient, login, org_a, db: AsyncSession
    ) -> None:
        """What the UI is told must equal what the backend will enforce."""
        from app.modules.audit.service import AuditRecorder

        headers = await login(f"accountant@{org_a.organization.code}.example.com", STAFF_PASSWORD)
        reported = set((await client.get("/api/v1/auth/me", headers=headers)).json()["permissions"])

        service = IdentityService(db, AuditRecorder(db, actor_label="test"))
        _, membership = org_a.members["accountant"]
        actual = set(await service.resolve_permissions(membership))
        assert reported == actual

    async def test_module_list_hides_modules_the_caller_cannot_see(
        self, client: AsyncClient, login, org_a
    ) -> None:
        headers = await login(f"receptionist@{org_a.organization.code}.example.com", STAFF_PASSWORD)
        modules = (await client.get("/api/v1/platform/modules", headers=headers)).json()
        by_key = {m["key"]: m for m in modules}
        # A receptionist has no payroll permission at all.
        assert by_key["payroll"]["visible_to_caller"] is False
        assert by_key["clinic"]["visible_to_caller"] is True
