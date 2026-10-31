"""Accounting policies: operation type → debit/credit account.

The successor to the legacy workbook's `Учетная_политика` sheet, with the
properties that sheet lacked. The one that matters most: a posted entry stores
the resolved account ids *and* the policy version that produced them, so
editing a policy cannot restate history. In the workbook the Dr/Cr columns are
`INDEX/MATCH` formulas, which means one cell edit silently rewrites what every
past entry appears to say (ADR-011).
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Conflict, NotFound, ValidationFailed
from app.modules.accounting.enums import PolicyStatus
from app.modules.accounting.models import Account, AccountingPolicy, PolicyRule
from app.modules.audit import events
from app.modules.audit.service import AuditRecorder

DEFAULT_POLICY_CODE = "default"


class PolicyService:
    def __init__(self, session: AsyncSession, audit: AuditRecorder) -> None:
        self._session = session
        self._audit = audit

    # ----------------------------------------------------------------- reads

    async def list_policies(self, organization_id: uuid.UUID) -> list[AccountingPolicy]:
        stmt = (
            select(AccountingPolicy)
            .where(AccountingPolicy.organization_id == organization_id)
            .order_by(AccountingPolicy.code, AccountingPolicy.version.desc())
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def get_policy(
        self, *, organization_id: uuid.UUID, policy_id: uuid.UUID
    ) -> AccountingPolicy:
        stmt = select(AccountingPolicy).where(
            AccountingPolicy.organization_id == organization_id,
            AccountingPolicy.id == policy_id,
        )
        policy = (await self._session.execute(stmt)).scalar_one_or_none()
        if policy is None:
            raise NotFound("Accounting policy not found")
        return policy

    async def list_rules(
        self, *, organization_id: uuid.UUID, policy_id: uuid.UUID
    ) -> list[PolicyRule]:
        stmt = (
            select(PolicyRule)
            .where(
                PolicyRule.organization_id == organization_id,
                PolicyRule.policy_id == policy_id,
            )
            .order_by(PolicyRule.operation_code)
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def policy_in_force_on(
        self, *, organization_id: uuid.UUID, on: date, code: str = DEFAULT_POLICY_CODE
    ) -> AccountingPolicy:
        """The version that was in force on a given date.

        Resolution is by effective date, not by "the current one". A posting
        back-dated into an earlier period must use the rules that applied
        then, so a SUPERSEDED version is still the right answer for a date
        inside its own window — that is what effective dating is for.

        Only DRAFT is excluded: a draft has never been in force.
        """
        stmt = (
            select(AccountingPolicy)
            .where(
                AccountingPolicy.organization_id == organization_id,
                AccountingPolicy.code == code,
                AccountingPolicy.status != PolicyStatus.DRAFT,
                AccountingPolicy.effective_from <= on,
                (AccountingPolicy.effective_to.is_(None)) | (AccountingPolicy.effective_to >= on),
            )
            .order_by(AccountingPolicy.version.desc())
            .limit(1)
        )
        policy = (await self._session.execute(stmt)).scalar_one_or_none()
        if policy is None:
            raise NotFound(
                f"No accounting policy {code!r} was in force on {on.isoformat()}",
                details={"policy_code": code, "date": on.isoformat()},
            )
        return policy

    # ---------------------------------------------------------------- writes

    async def create_draft(
        self,
        *,
        organization_id: uuid.UUID,
        name: str,
        effective_from: date,
        code: str = DEFAULT_POLICY_CODE,
        notes: str | None = None,
    ) -> AccountingPolicy:
        """Start a new version. Drafts post nothing until activated."""
        latest = (
            await self._session.execute(
                select(AccountingPolicy)
                .where(
                    AccountingPolicy.organization_id == organization_id,
                    AccountingPolicy.code == code,
                )
                .order_by(AccountingPolicy.version.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

        if latest is not None and latest.status is PolicyStatus.DRAFT:
            raise Conflict(
                f"Policy {code!r} already has an unactivated draft (version "
                f"{latest.version}). Edit or activate that one.",
                details={"policy_id": str(latest.id), "version": latest.version},
            )

        policy = AccountingPolicy(
            organization_id=organization_id,
            code=code,
            name=name,
            version=(latest.version + 1) if latest else 1,
            status=PolicyStatus.DRAFT,
            effective_from=effective_from,
            notes=notes,
        )
        self._session.add(policy)
        await self._session.flush()

        self._audit.record(
            events.POLICY_CHANGED,
            organization_id=organization_id,
            target_type="accounting_policy",
            target_id=policy.id,
            target_label=f"{code} v{policy.version}",
            new_values={
                "status": "DRAFT",
                "version": policy.version,
                "effective_from": effective_from,
            },
        )
        return policy

    async def set_rule(
        self,
        *,
        organization_id: uuid.UUID,
        policy_id: uuid.UUID,
        operation_code: str,
        description: str,
        debit_account_code: str,
        credit_account_code: str,
        description_uz: str | None = None,
        description_ru: str | None = None,
        required_documents: str | None = None,
    ) -> PolicyRule:
        """Add or replace one mapping. Only on a draft."""
        policy = await self.get_policy(organization_id=organization_id, policy_id=policy_id)
        if policy.status is not PolicyStatus.DRAFT:
            raise Conflict(
                f"Policy {policy.code} v{policy.version} is {policy.status.value.lower()} "
                "and cannot be edited. Create a new version instead.",
                details={"status": policy.status.value},
            )

        debit = await self._postable_account(organization_id, debit_account_code, "debit")
        credit = await self._postable_account(organization_id, credit_account_code, "credit")
        if debit.id == credit.id:
            raise ValidationFailed(
                "The debit and credit accounts must differ",
                details={"account_code": debit.code},
            )

        existing = (
            await self._session.execute(
                select(PolicyRule).where(
                    PolicyRule.organization_id == organization_id,
                    PolicyRule.policy_id == policy_id,
                    PolicyRule.operation_code == operation_code,
                )
            )
        ).scalar_one_or_none()

        if existing is not None:
            existing.description = description
            existing.description_uz = description_uz
            existing.description_ru = description_ru
            existing.debit_account_id = debit.id
            existing.credit_account_id = credit.id
            existing.required_documents = required_documents
            await self._session.flush()
            return existing

        rule = PolicyRule(
            organization_id=organization_id,
            policy_id=policy_id,
            operation_code=operation_code,
            description=description,
            description_uz=description_uz,
            description_ru=description_ru,
            debit_account_id=debit.id,
            credit_account_id=credit.id,
            required_documents=required_documents,
        )
        self._session.add(rule)
        await self._session.flush()
        return rule

    async def _postable_account(self, organization_id: uuid.UUID, code: str, side: str) -> Account:
        stmt = select(Account).where(
            Account.organization_id == organization_id, Account.code == code
        )
        account = (await self._session.execute(stmt)).scalar_one_or_none()
        if account is None:
            raise NotFound(
                f"The {side} account {code!r} does not exist",
                details={"account_code": code, "side": side},
            )
        if not account.is_postable:
            raise ValidationFailed(
                f"The {side} account {code} is a {account.level.value.lower()} "
                "and cannot be posted to",
                details={"account_code": code, "side": side},
            )
        if not account.is_active:
            raise ValidationFailed(
                f"The {side} account {code} is deactivated",
                details={"account_code": code, "side": side},
            )
        return account

    async def discard_draft(
        self, *, organization_id: uuid.UUID, policy_id: uuid.UUID
    ) -> AccountingPolicy:
        """Throw a draft away, with its rules.

        A draft has never posted anything, so nothing in the ledger refers to
        it and discarding it leaves no gap — the same reasoning that lets a
        draft journal entry be deleted while a posted one must be reversed.

        Without this a draft is a dead end: its effective date cannot be
        changed, a draft that starts before the version in force can never be
        activated, and a draft blocks every new version until it is resolved.
        """
        policy = await self.get_policy(organization_id=organization_id, policy_id=policy_id)
        if policy.status is not PolicyStatus.DRAFT:
            raise Conflict(
                f"Policy {policy.code} v{policy.version} is "
                f"{policy.status.value.lower()} and cannot be discarded. "
                "Supersede it with a new version instead.",
                details={"status": policy.status.value, "version": policy.version},
            )

        rules = await self.list_rules(organization_id=organization_id, policy_id=policy_id)
        self._audit.record(
            events.POLICY_DISCARDED,
            organization_id=organization_id,
            target_type="accounting_policy",
            target_id=policy.id,
            target_label=f"{policy.code} v{policy.version}",
            old_values={
                "status": policy.status.value,
                "version": policy.version,
                "effective_from": policy.effective_from,
                "rule_count": len(rules),
            },
        )
        for rule in rules:
            await self._session.delete(rule)
        await self._session.delete(policy)
        await self._session.flush()
        return policy

    async def activate(
        self,
        *,
        organization_id: uuid.UUID,
        policy_id: uuid.UUID,
        actor_user_id: uuid.UUID | None = None,
    ) -> AccountingPolicy:
        """Validate a draft and put it into force.

        Validation happens here rather than at posting time, so a broken rule
        is caught by the person editing the policy instead of by whoever makes
        the next sale.
        """
        policy = await self.get_policy(organization_id=organization_id, policy_id=policy_id)
        if policy.status is not PolicyStatus.DRAFT:
            raise Conflict(
                f"Policy {policy.code} v{policy.version} is already {policy.status.value.lower()}"
            )

        rules = await self.list_rules(organization_id=organization_id, policy_id=policy_id)
        if not rules:
            raise ValidationFailed(
                "A policy with no rules cannot be activated",
                details={"policy_id": str(policy_id)},
            )

        problems = await self._validate_rules(organization_id, rules)
        if problems:
            raise ValidationFailed(
                "This policy cannot be activated until its rules are corrected",
                details={"problems": problems},
            )

        # Close the previous version the day before this one starts, so the
        # two never both answer for the same date.
        previous = (
            (
                await self._session.execute(
                    select(AccountingPolicy).where(
                        AccountingPolicy.organization_id == organization_id,
                        AccountingPolicy.code == policy.code,
                        AccountingPolicy.status == PolicyStatus.ACTIVE,
                    )
                )
            )
            .scalars()
            .all()
        )
        # A version cannot start before the one it supersedes: closing the
        # prior version "the day before" would then end it before it began,
        # which `ck_accounting_policy_effective_range_ordered` refuses — as a
        # 500, long after the person could have been told what was wrong.
        too_early = [p for p in previous if p.effective_from >= policy.effective_from]
        if too_early:
            earliest = min(p.effective_from for p in too_early)
            raise ValidationFailed(
                f"This version starts on {policy.effective_from.isoformat()}, but the "
                f"version it would supersede is in force from {earliest.isoformat()}. "
                "A new version must start after the one it replaces.",
                details={
                    "effective_from": policy.effective_from.isoformat(),
                    "in_force_from": earliest.isoformat(),
                    "superseded_versions": [p.version for p in too_early],
                },
            )

        for prior in previous:
            prior.status = PolicyStatus.SUPERSEDED
            if prior.effective_to is None:
                prior.effective_to = policy.effective_from - timedelta(days=1)

        policy.status = PolicyStatus.ACTIVE
        policy.activated_at = datetime.now(UTC)
        policy.activated_by_user_id = actor_user_id
        await self._session.flush()

        self._audit.record(
            events.POLICY_ACTIVATED,
            organization_id=organization_id,
            target_type="accounting_policy",
            target_id=policy.id,
            target_label=f"{policy.code} v{policy.version}",
            old_values={"status": "DRAFT"},
            new_values={
                "status": "ACTIVE",
                "version": policy.version,
                "effective_from": policy.effective_from,
                "rule_count": len(rules),
                "superseded_versions": [p.version for p in previous],
            },
        )
        return policy

    async def _validate_rules(
        self, organization_id: uuid.UUID, rules: list[PolicyRule]
    ) -> list[str]:
        account_ids = {r.debit_account_id for r in rules} | {r.credit_account_id for r in rules}
        accounts = {
            a.id: a
            for a in (
                await self._session.execute(
                    select(Account).where(
                        Account.organization_id == organization_id,
                        Account.id.in_(account_ids),
                    )
                )
            ).scalars()
        }
        problems: list[str] = []
        for rule in rules:
            for side, account_id in (
                ("debit", rule.debit_account_id),
                ("credit", rule.credit_account_id),
            ):
                account = accounts.get(account_id)
                if account is None:
                    problems.append(f"{rule.operation_code}: the {side} account no longer exists")
                elif not account.is_postable:
                    problems.append(
                        f"{rule.operation_code}: {side} account {account.code} is not postable"
                    )
                elif not account.is_active:
                    problems.append(
                        f"{rule.operation_code}: {side} account {account.code} is deactivated"
                    )
            if rule.debit_account_id == rule.credit_account_id:
                problems.append(f"{rule.operation_code}: debit and credit are the same account")
        return problems
