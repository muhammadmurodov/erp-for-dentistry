"""Authentication behaviour, including what must *not* be revealed."""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.modules.audit import events
from app.modules.audit.models import AuditLog
from app.modules.identity.models import RefreshToken

pytestmark = pytest.mark.security

LOGIN = "/api/v1/auth/login"
FIXTURE_OWNER_PASSWORD = "fixture-owner-password-1"


class TestLogin:
    async def test_valid_credentials_return_tokens(self, client: AsyncClient, org_a) -> None:
        response = await client.post(
            LOGIN,
            json={"email": org_a.owner.email, "password": FIXTURE_OWNER_PASSWORD},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["token_type"] == "bearer"
        assert body["access_token"] and body["refresh_token"]
        assert body["expires_at"]

    async def test_wrong_password_is_rejected(self, client: AsyncClient, org_a) -> None:
        response = await client.post(
            LOGIN, json={"email": org_a.owner.email, "password": "not-the-password"}
        )
        assert response.status_code == 401

    async def test_unknown_and_wrong_password_are_indistinguishable(
        self, client: AsyncClient, org_a
    ) -> None:
        """Response content must not disclose whether an account exists."""
        unknown = await client.post(
            LOGIN, json={"email": "nobody@nowhere.example.com", "password": "whatever-123"}
        )
        wrong = await client.post(
            LOGIN, json={"email": org_a.owner.email, "password": "whatever-123"}
        )
        assert unknown.status_code == wrong.status_code == 401
        assert unknown.json()["message"] == wrong.json()["message"]
        assert unknown.json()["code"] == wrong.json()["code"]

    async def test_response_never_contains_the_password_hash(
        self, client: AsyncClient, org_a
    ) -> None:
        response = await client.post(
            LOGIN,
            json={"email": org_a.owner.email, "password": FIXTURE_OWNER_PASSWORD},
        )
        assert "argon2" not in response.text
        assert "password_hash" not in response.text

    async def test_email_is_case_insensitive(self, client: AsyncClient, org_a) -> None:
        response = await client.post(
            LOGIN,
            json={
                "email": org_a.owner.email.upper(),
                "password": FIXTURE_OWNER_PASSWORD,
            },
        )
        assert response.status_code == 200

    async def test_requesting_a_foreign_organization_is_denied(
        self, client: AsyncClient, org_a, org_b
    ) -> None:
        """Naming someone else's organization at sign-in must not work."""
        response = await client.post(
            LOGIN,
            json={
                "email": org_a.owner.email,
                "password": FIXTURE_OWNER_PASSWORD,
                "organization_id": str(org_b.id),
            },
        )
        assert response.status_code == 403
        # The reply must not confirm that the organization exists.
        assert str(org_b.id) not in response.text


class TestLockout:
    async def test_repeated_failures_lock_the_account(
        self, client: AsyncClient, org_a, db: AsyncSession
    ) -> None:
        settings = get_settings()
        for _ in range(settings.login_max_failed_attempts):
            await client.post(
                LOGIN, json={"email": org_a.owner.email, "password": "wrong-password-1"}
            )
        # Even the correct password is refused while the lock holds.
        response = await client.post(
            LOGIN,
            json={"email": org_a.owner.email, "password": FIXTURE_OWNER_PASSWORD},
        )
        assert response.status_code == 401
        assert "locked" in response.json()["message"].lower()

    async def test_successful_login_clears_the_failure_counter(
        self, client: AsyncClient, org_a, db: AsyncSession
    ) -> None:
        await client.post(LOGIN, json={"email": org_a.owner.email, "password": "wrong-password-1"})
        ok = await client.post(
            LOGIN,
            json={"email": org_a.owner.email, "password": FIXTURE_OWNER_PASSWORD},
        )
        assert ok.status_code == 200
        await db.refresh(org_a.owner)
        assert org_a.owner.failed_login_count == 0


class TestRefreshRotation:
    async def test_refresh_returns_a_new_pair(self, client: AsyncClient, org_a) -> None:
        login = await client.post(
            LOGIN,
            json={"email": org_a.owner.email, "password": FIXTURE_OWNER_PASSWORD},
        )
        first = login.json()["refresh_token"]
        response = await client.post("/api/v1/auth/refresh", json={"refresh_token": first})
        assert response.status_code == 200
        assert response.json()["refresh_token"] != first

    async def test_replaying_a_rotated_token_ends_every_session(
        self, client: AsyncClient, org_a, db: AsyncSession
    ) -> None:
        """A rotated token coming back is the signature of a stolen token."""
        login = await client.post(
            LOGIN,
            json={"email": org_a.owner.email, "password": FIXTURE_OWNER_PASSWORD},
        )
        stolen = login.json()["refresh_token"]
        rotated = await client.post("/api/v1/auth/refresh", json={"refresh_token": stolen})
        fresh = rotated.json()["refresh_token"]

        replay = await client.post("/api/v1/auth/refresh", json={"refresh_token": stolen})
        assert replay.status_code == 401

        # The chain is burned: the legitimate token is dead too.
        after = await client.post("/api/v1/auth/refresh", json={"refresh_token": fresh})
        assert after.status_code == 401

        live = (
            (
                await db.execute(
                    select(RefreshToken).where(
                        RefreshToken.user_id == org_a.owner.id,
                        RefreshToken.revoked_at.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        assert not live

    async def test_unknown_refresh_token_is_rejected(self, client: AsyncClient) -> None:
        response = await client.post("/api/v1/auth/refresh", json={"refresh_token": "x" * 64})
        assert response.status_code == 401

    async def test_logout_revokes_the_session(self, client: AsyncClient, org_a) -> None:
        login = await client.post(
            LOGIN,
            json={"email": org_a.owner.email, "password": FIXTURE_OWNER_PASSWORD},
        )
        token = login.json()["refresh_token"]
        assert (
            await client.post("/api/v1/auth/logout", json={"refresh_token": token})
        ).status_code == 204
        assert (
            await client.post("/api/v1/auth/refresh", json={"refresh_token": token})
        ).status_code == 401

    async def test_logout_is_idempotent(self, client: AsyncClient, org_a) -> None:
        login = await client.post(
            LOGIN,
            json={"email": org_a.owner.email, "password": FIXTURE_OWNER_PASSWORD},
        )
        token = login.json()["refresh_token"]
        for _ in range(2):
            response = await client.post("/api/v1/auth/logout", json={"refresh_token": token})
            assert response.status_code == 204


class TestTokenRequirement:
    async def test_protected_endpoint_requires_a_token(self, client: AsyncClient) -> None:
        response = await client.get("/api/v1/organization")
        assert response.status_code == 401
        assert response.headers["WWW-Authenticate"] == "Bearer"

    async def test_garbage_token_is_rejected(self, client: AsyncClient) -> None:
        response = await client.get(
            "/api/v1/organization",
            headers={"Authorization": "Bearer not.a.real.token"},
        )
        assert response.status_code == 401

    async def test_token_signed_with_another_key_is_rejected(
        self, client: AsyncClient, org_a
    ) -> None:
        from jose import jwt

        forged = jwt.encode(
            {
                "sub": str(org_a.owner.id),
                "typ": "access",
                "org": str(org_a.id),
                "prm": ["organization.read"],
                "exp": 9_999_999_999,
            },
            "a-different-secret-key-entirely",
            algorithm="HS256",
        )
        response = await client.get(
            "/api/v1/organization", headers={"Authorization": f"Bearer {forged}"}
        )
        assert response.status_code == 401


class TestAuditOfAuthentication:
    async def test_successful_login_is_audited(
        self, client: AsyncClient, org_a, db: AsyncSession
    ) -> None:
        await client.post(
            LOGIN,
            json={"email": org_a.owner.email, "password": FIXTURE_OWNER_PASSWORD},
        )
        rows = (
            (await db.execute(select(AuditLog).where(AuditLog.action == events.LOGIN_SUCCEEDED)))
            .scalars()
            .all()
        )
        assert any(r.actor_label == org_a.owner.email for r in rows)

    async def test_failed_login_is_audited_with_the_reason(
        self, client: AsyncClient, org_a, db: AsyncSession
    ) -> None:
        await client.post(LOGIN, json={"email": org_a.owner.email, "password": "wrong-password-1"})
        rows = (
            (await db.execute(select(AuditLog).where(AuditLog.action == events.LOGIN_FAILED)))
            .scalars()
            .all()
        )
        assert rows
        row = rows[-1]
        assert row.outcome == "failure"
        # The audit trail may say which check failed; the HTTP response may not.
        assert row.reason == "wrong password"

    async def test_audit_record_never_stores_the_password(
        self, client: AsyncClient, org_a, db: AsyncSession
    ) -> None:
        secret = "a-very-distinctive-password-9"
        await client.post(LOGIN, json={"email": org_a.owner.email, "password": secret})
        rows = (await db.execute(select(AuditLog))).scalars().all()
        for row in rows:
            assert secret not in str(row.old_values)
            assert secret not in str(row.new_values)
            assert secret not in (row.reason or "")
