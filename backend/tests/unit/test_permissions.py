"""The permission catalogue and role definitions must stay internally sound."""

from __future__ import annotations

import pytest

from app.core.permissions import (
    ALL_PERMISSION_CODES,
    PERMISSIONS,
    READ_ONLY_ROLE_CODES,
    SEGREGATION_OF_DUTIES,
    SYSTEM_ROLES,
    check_role_permission_set,
    is_write_permission,
)


class TestCatalogue:
    def test_codes_are_unique(self) -> None:
        codes = [p.code for p in PERMISSIONS]
        assert len(codes) == len(set(codes))

    def test_codes_follow_the_naming_convention(self) -> None:
        for permission in PERMISSIONS:
            parts = permission.code.split(".")
            assert len(parts) >= 2, permission.code
            assert parts[0] == permission.module, permission.code

    def test_every_permission_is_described(self) -> None:
        for permission in PERMISSIONS:
            assert len(permission.description) > 10, permission.code

    def test_sensitive_actions_are_marked(self) -> None:
        """Posting, approving and reversing are financially material."""
        for code in (
            "accounting.journal.post",
            "accounting.journal.reverse",
            "accounting.policy.manage",
            "payroll.run.approve",
            "sales.refund.approve",
            "cash.discrepancy.approve",
            "clinic.clinical.read",
            "audit.log.read",
        ):
            permission = next(p for p in PERMISSIONS if p.code == code)
            assert permission.sensitive, f"{code} should be marked sensitive"


class TestSystemRoles:
    def test_role_codes_are_unique(self) -> None:
        codes = [r.code for r in SYSTEM_ROLES]
        assert len(codes) == len(set(codes))

    def test_every_role_grants_only_known_permissions(self) -> None:
        for role in SYSTEM_ROLES:
            unknown = set(role.permissions) - ALL_PERMISSION_CODES
            assert not unknown, f"{role.code} grants unknown {unknown}"

    def test_no_system_role_breaks_segregation_of_duties(self) -> None:
        """The control is worthless if the shipped roles already violate it."""
        for role in SYSTEM_ROLES:
            problems = check_role_permission_set(role.code, set(role.permissions))
            assert not problems, f"{role.code}: {problems}"

    def test_owner_holds_every_permission(self) -> None:
        owner = next(r for r in SYSTEM_ROLES if r.code == "owner")
        assert set(owner.permissions) == ALL_PERMISSION_CODES

    def test_auditor_is_strictly_read_only(self) -> None:
        auditor = next(r for r in SYSTEM_ROLES if r.code == "auditor")
        writes = {c for c in auditor.permissions if is_write_permission(c)}
        assert not writes, f"auditor must not write: {writes}"
        assert "auditor" in READ_ONLY_ROLE_CODES

    def test_cashier_cannot_touch_accounting_policy_or_post(self) -> None:
        cashier = next(r for r in SYSTEM_ROLES if r.code == "cashier")
        for forbidden in (
            "accounting.policy.manage",
            "accounting.journal.post",
            "accounting.journal.reverse",
            "payroll.run.approve",
        ):
            assert forbidden not in cashier.permissions

    def test_cashier_can_record_payments(self) -> None:
        cashier = next(r for r in SYSTEM_ROLES if r.code == "cashier")
        assert "sales.payment.record" in cashier.permissions
        assert "cash.session.operate" in cashier.permissions

    def test_accountant_may_post_but_not_rewrite_policy(self) -> None:
        accountant = next(r for r in SYSTEM_ROLES if r.code == "accountant")
        assert "accounting.journal.post" in accountant.permissions
        assert "accounting.policy.manage" not in accountant.permissions

    def test_no_role_but_owner_manages_accounting_policy(self) -> None:
        """Changing a Dr/Cr mapping is an owner-level act."""
        holders = {r.code for r in SYSTEM_ROLES if "accounting.policy.manage" in r.permissions}
        assert holders == {"owner"}

    def test_financial_roles_do_not_read_clinical_records(self) -> None:
        """Financial staff get no automatic access to clinical detail."""
        for code in ("accountant", "cashier", "auditor", "receptionist"):
            role = next(r for r in SYSTEM_ROLES if r.code == code)
            assert "clinic.clinical.read" not in role.permissions, code
            assert "clinic.clinical.write" not in role.permissions, code

    def test_dentist_reads_clinical_but_not_the_ledger(self) -> None:
        dentist = next(r for r in SYSTEM_ROLES if r.code == "dentist")
        assert "clinic.clinical.read" in dentist.permissions
        assert "accounting.journal.post" not in dentist.permissions
        assert "accounting.journal.read" not in dentist.permissions


class TestSegregationOfDuties:
    def test_pairs_reference_real_permissions(self) -> None:
        for left, right, reason in SEGREGATION_OF_DUTIES:
            assert left in ALL_PERMISSION_CODES, left
            assert right in ALL_PERMISSION_CODES, right
            assert reason

    @pytest.mark.parametrize("left,right,_", SEGREGATION_OF_DUTIES)
    def test_conflicting_pair_is_rejected(self, left, right, _) -> None:
        problems = check_role_permission_set("custom_role", {left, right})
        assert problems, f"{left} + {right} should conflict"

    def test_owner_is_exempt(self) -> None:
        """A single-owner MChJ has nobody to delegate approval to."""
        for left, right, _ in SEGREGATION_OF_DUTIES:
            assert not check_role_permission_set("owner", {left, right})

    def test_unknown_permission_is_reported(self) -> None:
        problems = check_role_permission_set("custom_role", {"made.up.permission"})
        assert any("Unknown" in p for p in problems)
