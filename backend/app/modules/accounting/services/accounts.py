"""The chart of accounts.

Seeding imports the statutory Uzbek chart from `app.reference`. Every seeded
account is marked `needs_confirmation`, because its classification was derived
from code ranges rather than read from the source — see ADR-017 and
OPEN_QUESTIONS A1. The interface shows that state rather than presenting
derived data as authoritative.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Conflict, NotFound, ValidationFailed
from app.core.money import ZERO
from app.modules.accounting.enums import (
    POSTABLE_LEVELS,
    AccountElement,
    AccountLevel,
    AccountNature,
    EntryStatus,
)
from app.modules.accounting.models import Account, JournalLine
from app.modules.audit import events
from app.modules.audit.service import AuditRecorder
from app.reference.loader import load_chart_of_accounts


class AccountService:
    def __init__(self, session: AsyncSession, audit: AuditRecorder) -> None:
        self._session = session
        self._audit = audit

    # ----------------------------------------------------------------- reads

    async def list_accounts(
        self,
        *,
        organization_id: uuid.UUID,
        postable_only: bool = False,
        active_only: bool = True,
        search: str | None = None,
    ) -> list[Account]:
        stmt = select(Account).where(Account.organization_id == organization_id)
        if postable_only:
            stmt = stmt.where(Account.is_postable.is_(True))
        if active_only:
            stmt = stmt.where(Account.is_active.is_(True))
        if search:
            needle = f"%{search.strip()}%"
            stmt = stmt.where(Account.code.ilike(needle) | Account.name.ilike(needle))
        return list((await self._session.execute(stmt.order_by(Account.code))).scalars().all())

    async def get_account(self, *, organization_id: uuid.UUID, account_id: uuid.UUID) -> Account:
        stmt = select(Account).where(
            Account.organization_id == organization_id, Account.id == account_id
        )
        account = (await self._session.execute(stmt)).scalar_one_or_none()
        if account is None:
            raise NotFound("Account not found")
        return account

    async def get_by_code(self, *, organization_id: uuid.UUID, code: str) -> Account | None:
        stmt = select(Account).where(
            Account.organization_id == organization_id, Account.code == code
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def count(self, organization_id: uuid.UUID) -> int:
        stmt = (
            select(func.count())
            .select_from(Account)
            .where(Account.organization_id == organization_id)
        )
        return int((await self._session.execute(stmt)).scalar_one())

    # ---------------------------------------------------------------- writes

    async def create_account(
        self,
        *,
        organization_id: uuid.UUID,
        code: str,
        name: str,
        level: AccountLevel,
        nature: AccountNature | None = None,
        element: AccountElement | None = None,
        parent_code: str | None = None,
        description: str | None = None,
    ) -> Account:
        code = code.strip()
        if not code:
            raise ValidationFailed("An account needs a code")

        if await self.get_by_code(organization_id=organization_id, code=code):
            raise Conflict(f"Account {code!r} already exists")

        parent: Account | None = None
        if parent_code:
            parent = await self.get_by_code(organization_id=organization_id, code=parent_code)
            if parent is None:
                raise NotFound(f"Parent account {parent_code!r} does not exist")
            if parent.is_postable:
                # Otherwise the parent's own postings and its children's would
                # both roll up, double-counting the parent.
                raise ValidationFailed(
                    f"Account {parent.code} is postable and cannot also be a parent",
                    details={"parent_code": parent.code},
                )

        account = Account(
            organization_id=organization_id,
            code=code,
            name=name.strip(),
            level=level,
            nature=nature,
            element=element,
            parent_id=parent.id if parent else None,
            is_postable=level in POSTABLE_LEVELS,
            is_active=True,
            # Entered by hand by an authorised user, so it is not provisional.
            needs_confirmation=False,
            description=description,
        )
        self._session.add(account)
        await self._session.flush()

        self._audit.record(
            events.ACCOUNT_CREATED,
            organization_id=organization_id,
            target_type="account",
            target_id=account.id,
            target_label=code,
            new_values={"code": code, "name": name, "level": level.value},
        )
        return account

    async def set_active(
        self,
        *,
        organization_id: uuid.UUID,
        account_id: uuid.UUID,
        is_active: bool,
        reason: str | None = None,
    ) -> Account:
        """Activate or deactivate. Accounts are never deleted.

        A posted line references an account permanently; removing one would
        orphan history. Deactivating keeps the history readable while stopping
        new postings.
        """
        account = await self.get_account(organization_id=organization_id, account_id=account_id)
        if account.is_active == is_active:
            return account

        if not is_active:
            balance = await self.balance(organization_id=organization_id, account_id=account_id)
            if balance != ZERO:
                raise Conflict(
                    f"Account {account.code} still has a balance of {balance}. "
                    "Clear it before deactivating.",
                    details={"code": account.code, "balance": str(balance)},
                )

        account.is_active = is_active
        await self._session.flush()
        self._audit.record(
            events.ACCOUNT_DEACTIVATED if not is_active else events.ACCOUNT_CREATED,
            organization_id=organization_id,
            target_type="account",
            target_id=account.id,
            target_label=account.code,
            reason=reason,
            old_values={"is_active": not is_active},
            new_values={"is_active": is_active},
        )
        return account

    async def balance(self, *, organization_id: uuid.UUID, account_id: uuid.UUID) -> Decimal:
        """Net balance from posted lines only. Drafts never affect a balance."""
        stmt = select(
            func.coalesce(func.sum(JournalLine.debit), 0)
            - func.coalesce(func.sum(JournalLine.credit), 0)
        ).where(
            JournalLine.organization_id == organization_id,
            JournalLine.account_id == account_id,
            JournalLine.entry_status == EntryStatus.POSTED,
        )
        return Decimal((await self._session.execute(stmt)).scalar_one() or 0)

    # ---------------------------------------------------------------- seeding

    async def seed_statutory_chart(
        self,
        *,
        organization_id: uuid.UUID,
        only_commonly_used: bool = False,
        actor_user_id: uuid.UUID | None = None,
    ) -> dict[str, int]:
        """Import the Uzbek national chart of accounts.

        Deliberately a command rather than a migration side effect: adopting a
        368-row statutory chart is a decision an organization makes, and it is
        audited as one.

        Parents are created before children — the reference data is already in
        hierarchy order, and a second pass links any parent that was not.
        """
        if await self.count(organization_id) > 0:
            raise Conflict(
                "This organization already has a chart of accounts. "
                "Seeding twice would duplicate it."
            )

        reference = load_chart_of_accounts()
        if only_commonly_used:
            # Keep the structural rows regardless: dropping a parent would
            # orphan the accounts beneath it.
            keep = {a.code for a in reference if a.commonly_used}
            reference = tuple(
                a for a in reference if a.commonly_used or a.level not in POSTABLE_LEVELS
            )
            del keep

        by_code: dict[str, Account] = {}
        for row in reference:
            account = Account(
                organization_id=organization_id,
                code=row.code,
                name=row.name_uz_cyrillic,
                name_uz=row.name_uz_cyrillic,
                level=AccountLevel(row.level),
                nature=AccountNature(row.nature) if row.nature else None,
                element=AccountElement(row.element) if row.element else None,
                statutory_part=row.statutory_part,
                is_postable=row.level in {lvl.value for lvl in POSTABLE_LEVELS},
                is_active=True,
                # Derived classification: unconfirmed until an accountant
                # signs it off (OPEN_QUESTIONS A1).
                needs_confirmation=row.needs_confirmation,
            )
            self._session.add(account)
            by_code[row.code] = account
        await self._session.flush()

        linked = 0
        for row in reference:
            if row.parent_code and row.parent_code in by_code:
                by_code[row.code].parent_id = by_code[row.parent_code].id
                linked += 1
        await self._session.flush()

        postable = sum(1 for a in by_code.values() if a.is_postable)
        summary = {
            "accounts": len(by_code),
            "postable": postable,
            "structural": len(by_code) - postable,
            "parents_linked": linked,
        }
        self._audit.record(
            "accounting.chart.seeded",
            organization_id=organization_id,
            target_type="account",
            target_label="statutory chart of accounts",
            actor_user_id=actor_user_id,
            new_values={
                **summary,
                "source": "Uzbek national chart of accounts (NSBU)",
                "needs_confirmation": True,
            },
        )
        return summary
