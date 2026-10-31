"""Bank accounts, and the ledger account each one answers to.

A bank account here is a *pair*: the account at the bank and the ledger
account whose balance it is supposed to equal. Reconciliation is the exercise
of proving those two agree, which is only meaningful if the pairing is
recorded rather than assumed.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Conflict, NotFound, ValidationFailed
from app.modules.accounting.services.accounts import AccountService
from app.modules.audit.service import AuditRecorder
from app.modules.banking.models import BankAccount


def masked_account_number(number: str | None) -> str | None:
    """Show enough to recognise the account, not enough to use it."""
    if not number:
        return None
    cleaned = number.strip()
    if len(cleaned) <= 4:
        return "*" * len(cleaned)
    return "*" * (len(cleaned) - 4) + cleaned[-4:]


class BankAccountService:
    def __init__(self, session: AsyncSession, audit: AuditRecorder) -> None:
        self._session = session
        self._audit = audit

    async def list_accounts(
        self, *, organization_id: uuid.UUID, include_inactive: bool = False
    ) -> list[BankAccount]:
        stmt = select(BankAccount).where(BankAccount.organization_id == organization_id)
        if not include_inactive:
            stmt = stmt.where(BankAccount.is_active.is_(True))
        return list((await self._session.execute(stmt.order_by(BankAccount.code))).scalars().all())

    async def get(self, *, organization_id: uuid.UUID, bank_account_id: uuid.UUID) -> BankAccount:
        stmt = select(BankAccount).where(
            BankAccount.organization_id == organization_id,
            BankAccount.id == bank_account_id,
        )
        account = (await self._session.execute(stmt)).scalar_one_or_none()
        if account is None:
            raise NotFound("Bank account not found")
        return account

    async def create(
        self,
        *,
        organization_id: uuid.UUID,
        code: str,
        name: str,
        account_code: str,
        bank_name: str | None = None,
        mfo: str | None = None,
        account_number: str | None = None,
        currency: str = "UZS",
        column_mapping: dict | None = None,
    ) -> BankAccount:
        ledger = await AccountService(self._session, self._audit).get_by_code(
            organization_id=organization_id, code=account_code
        )
        if ledger is None:
            raise NotFound(f"Account {account_code!r} does not exist")
        if not ledger.is_postable:
            raise ValidationFailed(
                f"Account {account_code} is a {ledger.level.value.lower()} and cannot "
                "hold a bank balance"
            )
        if (
            mfo is not None
            and mfo.strip()
            and not (mfo.strip().isdigit() and len(mfo.strip()) == 5)
        ):
            raise ValidationFailed("An МФО code is exactly five digits")

        existing = (
            await self._session.execute(
                select(BankAccount).where(
                    BankAccount.organization_id == organization_id,
                    BankAccount.code == code.strip(),
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            raise Conflict(f"A bank account with code {code.strip()!r} already exists")

        account = BankAccount(
            organization_id=organization_id,
            code=code.strip(),
            name=name.strip(),
            bank_name=(bank_name or "").strip() or None,
            mfo=(mfo or "").strip() or None,
            account_number=(account_number or "").strip() or None,
            account_id=ledger.id,
            currency=currency,
            column_mapping=column_mapping or None,
        )
        self._session.add(account)
        await self._session.flush()

        # The account number is deliberately absent: it is a payment
        # credential and the audit trail is read by more people than the
        # bank screen is.
        self._audit.record(
            "banking.account.created",
            organization_id=organization_id,
            target_type="bank_account",
            target_id=account.id,
            target_label=account.code,
            new_values={
                "code": account.code,
                "name": account.name,
                "bank_name": account.bank_name,
                "mfo": account.mfo,
                "ledger_account": account_code,
                "currency": account.currency,
            },
        )
        return account

    async def set_column_mapping(
        self,
        *,
        organization_id: uuid.UUID,
        bank_account_id: uuid.UUID,
        column_mapping: dict | None,
    ) -> BankAccount:
        """Teach this account what its bank's export columns are called."""
        account = await self.get(organization_id=organization_id, bank_account_id=bank_account_id)
        previous = account.column_mapping
        account.column_mapping = column_mapping or None
        await self._session.flush()

        self._audit.record(
            "banking.account.mapping_changed",
            organization_id=organization_id,
            target_type="bank_account",
            target_id=account.id,
            target_label=account.code,
            old_values={"column_mapping": previous},
            new_values={"column_mapping": account.column_mapping},
        )
        return account

    async def deactivate(
        self, *, organization_id: uuid.UUID, bank_account_id: uuid.UUID, reason: str
    ) -> BankAccount:
        if not reason.strip():
            raise ValidationFailed("Deactivating a bank account requires a reason")
        account = await self.get(organization_id=organization_id, bank_account_id=bank_account_id)
        if not account.is_active:
            raise Conflict("That bank account is already inactive")
        account.is_active = False
        await self._session.flush()

        self._audit.record(
            "banking.account.deactivated",
            organization_id=organization_id,
            target_type="bank_account",
            target_id=account.id,
            target_label=account.code,
            reason=reason.strip(),
            new_values={"is_active": False},
        )
        return account
