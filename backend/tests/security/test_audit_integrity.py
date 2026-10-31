"""The audit trail must be append-only and must not leak secrets."""

from __future__ import annotations

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit import events
from app.modules.audit.models import AuditLog
from app.modules.audit.service import AuditRecorder, changed_fields

pytestmark = pytest.mark.security


class TestAppendOnly:
    async def test_update_is_rejected_by_the_database(
        self, db: AsyncSession, audit: AuditRecorder, org_a
    ) -> None:
        """Not an application check — the database refuses it."""
        audit.record(
            events.ORGANIZATION_UPDATED,
            organization_id=org_a.id,
            target_type="organization",
            target_id=org_a.id,
        )
        await db.flush()
        row = (
            await db.execute(select(AuditLog).where(AuditLog.organization_id == org_a.id).limit(1))
        ).scalar_one()

        await db.begin_nested()
        with pytest.raises(DBAPIError) as caught:
            await db.execute(
                text("UPDATE audit_log SET action = 'tampered' WHERE id = :id"),
                {"id": row.id},
            )
        assert "append-only" in str(caught.value)
        await db.rollback()

    async def test_delete_is_rejected_by_the_database(
        self, db: AsyncSession, audit: AuditRecorder, org_a
    ) -> None:
        audit.record(
            events.ORGANIZATION_UPDATED,
            organization_id=org_a.id,
            target_type="organization",
            target_id=org_a.id,
        )
        await db.flush()

        await db.begin_nested()
        with pytest.raises(DBAPIError) as caught:
            await db.execute(
                text("DELETE FROM audit_log WHERE organization_id = :org"),
                {"org": org_a.id},
            )
        assert "append-only" in str(caught.value)
        await db.rollback()

    async def test_the_guard_trigger_is_installed(self, db: AsyncSession) -> None:
        """Fails loudly if a future migration drops the protection."""
        triggers = (
            (
                await db.execute(
                    text(
                        """
                    SELECT tgname FROM pg_trigger
                    WHERE tgrelid = 'audit_log'::regclass AND NOT tgisinternal
                    """
                    )
                )
            )
            .scalars()
            .all()
        )
        assert "trg_audit_log_append_only" in triggers

    async def test_organization_cannot_be_deleted_while_audited(
        self, db: AsyncSession, audit: AuditRecorder, org_a
    ) -> None:
        """RESTRICT on the audit FK keeps history from being orphaned."""
        audit.record(
            events.ORGANIZATION_UPDATED,
            organization_id=org_a.id,
            target_type="organization",
            target_id=org_a.id,
        )
        await db.flush()

        await db.begin_nested()
        with pytest.raises(DBAPIError):
            await db.execute(text("DELETE FROM organization WHERE id = :id"), {"id": org_a.id})
        await db.rollback()


class TestRedaction:
    async def test_sensitive_keys_are_redacted_before_storage(
        self, db: AsyncSession, audit: AuditRecorder, org_a
    ) -> None:
        audit.record(
            events.USER_UPDATED,
            organization_id=org_a.id,
            target_type="app_user",
            new_values={
                "email": "person@example.com",
                "password": "should-never-be-stored",
                "refresh_token": "should-never-be-stored",
                "diagnosis": "should-never-be-stored",
            },
        )
        await db.flush()
        row = (
            await db.execute(
                select(AuditLog).where(AuditLog.action == events.USER_UPDATED).limit(1)
            )
        ).scalar_one()
        assert row.new_values["email"] == "person@example.com"
        for key in ("password", "refresh_token", "diagnosis"):
            assert row.new_values[key] == "[redacted]", key

    async def test_decimals_are_stored_as_exact_text(
        self, db: AsyncSession, audit: AuditRecorder, org_a
    ) -> None:
        """A monetary value in an audit record must not become a float."""
        from decimal import Decimal

        audit.record(
            events.PAYMENT_RECORDED,
            organization_id=org_a.id,
            new_values={"amount": Decimal("300000.05")},
        )
        await db.flush()
        row = (
            await db.execute(select(AuditLog).where(AuditLog.action == events.PAYMENT_RECORDED))
        ).scalar_one()
        assert row.new_values["amount"] == "300000.05"
        assert isinstance(row.new_values["amount"], str)

    async def test_oversized_values_are_truncated(
        self, db: AsyncSession, audit: AuditRecorder, org_a
    ) -> None:
        audit.record(
            events.USER_UPDATED,
            organization_id=org_a.id,
            new_values={"note": "x" * 5000},
        )
        await db.flush()
        row = (
            await db.execute(select(AuditLog).where(AuditLog.action == events.USER_UPDATED))
        ).scalar_one()
        assert len(row.new_values["note"]) < 5000


class TestChangedFields:
    def test_only_differences_are_reported(self) -> None:
        old, new = changed_fields(
            {"a": 1, "b": "same", "c": None}, {"a": 2, "b": "same", "c": "set"}
        )
        assert old == {"a": 1, "c": None}
        assert new == {"a": 2, "c": "set"}

    def test_identical_snapshots_produce_nothing(self) -> None:
        old, new = changed_fields({"a": 1}, {"a": 1})
        assert old == {} and new == {}


class TestEventCatalogue:
    def test_required_actions_all_exist_in_the_catalogue(self) -> None:
        declared = {
            value
            for name, value in vars(events).items()
            if name.isupper() and isinstance(value, str)
        }
        assert declared >= events.REQUIRED_AUDIT_ACTIONS

    def test_action_names_are_namespaced(self) -> None:
        for name, value in vars(events).items():
            if name.isupper() and isinstance(value, str):
                assert "." in value, f"{name} should be dotted"
                assert value == value.lower(), f"{name} should be lowercase"
