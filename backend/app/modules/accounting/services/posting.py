"""The posting engine — the only way anything reaches the ledger.

Every accounting invariant is checked here, and again by the database. The
duplication is deliberate: the service produces a clear error message, and the
schema guarantees correctness even if a future caller bypasses the service or
a check is accidentally removed.

Other modules depend on `PostingRequest` and `PostingService.post`. They never
construct a `JournalEntry`.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Literal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import (
    Conflict,
    DuplicatePostingEvent,
    EntryImmutable,
    NotFound,
    PeriodClosed,
    UnbalancedEntry,
    ValidationFailed,
)
from app.core.logging import get_logger
from app.core.money import ZERO, quantize, to_amount
from app.modules.accounting.enums import (
    EntryStatus,
    PeriodStatus,
    PostingPurpose,
    SourceKind,
)
from app.modules.accounting.models import (
    Account,
    AccountingPeriod,
    JournalEntry,
    JournalLine,
    PolicyRule,
    PostingEvent,
)
from app.modules.accounting.services.numbering import next_number
from app.modules.audit import events
from app.modules.audit.service import AuditRecorder

log = get_logger(__name__)

#: A balanced entry needs at least one debit and one credit.
MINIMUM_LINES = 2


@dataclass(frozen=True, slots=True)
class PostingLine:
    """One side of a posting, before it becomes a journal line.

    Identify the account by `account_id` or by `account_code`; exactly one.
    Code is the ergonomic choice for a caller that knows the chart, id for one
    that already resolved it.
    """

    debit: Decimal = ZERO
    credit: Decimal = ZERO
    account_id: uuid.UUID | None = None
    account_code: str | None = None
    description: str | None = None

    def __post_init__(self) -> None:
        if (self.account_id is None) == (self.account_code is None):
            raise ValidationFailed("A posting line needs exactly one of account_id or account_code")


@dataclass(frozen=True, slots=True)
class PostingRequest:
    """A business event, expressed as a balanced set of lines.

    `source_kind`, `source_id` and `purpose` together form the idempotency
    key. A card payment legitimately produces two entries — the revenue
    recognition and the later settlement — so the purpose is part of the key
    rather than the source alone.
    """

    entry_date: date
    description: str
    source_kind: SourceKind
    lines: list[PostingLine]
    purpose: PostingPurpose = PostingPurpose.MANUAL
    source_id: str | None = None
    currency: str = "UZS"
    operation_code: str | None = None
    policy_id: uuid.UUID | None = None
    reason: str | None = None
    #: Extra context for the audit record, never for the ledger itself.
    audit_context: dict = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ResolvedLine:
    account: Account
    debit: Decimal
    credit: Decimal
    description: str | None


class PostingService:
    """Creates, posts and reverses journal entries."""

    def __init__(self, session: AsyncSession, audit: AuditRecorder) -> None:
        self._session = session
        self._audit = audit

    # ------------------------------------------------------------ resolution

    async def resolve_period(self, *, organization_id: uuid.UUID, on: date) -> AccountingPeriod:
        """Find the period covering `on`, or explain that none does."""
        stmt = select(AccountingPeriod).where(
            AccountingPeriod.organization_id == organization_id,
            AccountingPeriod.starts_on <= on,
            AccountingPeriod.ends_on >= on,
        )
        period = (await self._session.execute(stmt)).scalar_one_or_none()
        if period is None:
            raise ValidationFailed(
                f"No accounting period covers {on.isoformat()}. "
                "Create the fiscal year and its periods first.",
                details={"entry_date": on.isoformat()},
            )
        return period

    async def _resolve_accounts(
        self, *, organization_id: uuid.UUID, lines: list[PostingLine]
    ) -> list[ResolvedLine]:
        codes = {line.account_code for line in lines if line.account_code}
        ids = {line.account_id for line in lines if line.account_id}

        by_code: dict[str, Account] = {}
        by_id: dict[uuid.UUID, Account] = {}

        if codes:
            stmt = select(Account).where(
                Account.organization_id == organization_id, Account.code.in_(codes)
            )
            for account in (await self._session.execute(stmt)).scalars():
                by_code[account.code] = account
        if ids:
            stmt = select(Account).where(
                Account.organization_id == organization_id, Account.id.in_(ids)
            )
            for account in (await self._session.execute(stmt)).scalars():
                by_id[account.id] = account

        resolved: list[ResolvedLine] = []
        problems: list[str] = []
        for index, line in enumerate(lines, start=1):
            account = (
                by_code.get(line.account_code) if line.account_code else by_id.get(line.account_id)  # type: ignore[arg-type]
            )
            reference = line.account_code or str(line.account_id)
            if account is None:
                problems.append(f"line {index}: account {reference!r} does not exist")
                continue
            if not account.is_postable:
                # A part, section or group aggregates its children; posting to
                # one would double-count it in a trial balance.
                problems.append(
                    f"line {index}: account {account.code} is a {account.level.value.lower()} "
                    "and cannot be posted to"
                )
                continue
            if not account.is_active:
                problems.append(f"line {index}: account {account.code} is deactivated")
                continue
            resolved.append(
                ResolvedLine(
                    account=account,
                    debit=line.debit,
                    credit=line.credit,
                    description=line.description,
                )
            )

        if problems:
            raise ValidationFailed(
                "The posting references accounts that cannot be used",
                details={"problems": problems},
            )
        return resolved

    # ------------------------------------------------------------ validation

    @staticmethod
    def _validate_amounts(resolved: list[ResolvedLine], currency: str) -> list[ResolvedLine]:
        """Quantize every amount and prove the entry balances.

        Comparison is exact `Decimal` equality after quantizing to the
        currency's scale. Comparing unquantized values would let two amounts
        that round to the same figure be treated as different.
        """
        if len(resolved) < MINIMUM_LINES:
            raise UnbalancedEntry(
                f"A journal entry needs at least {MINIMUM_LINES} lines, got {len(resolved)}"
            )

        quantized: list[ResolvedLine] = []
        problems: list[str] = []
        for index, line in enumerate(resolved, start=1):
            debit = quantize(to_amount(line.debit), currency)
            credit = quantize(to_amount(line.credit), currency)
            if debit < ZERO or credit < ZERO:
                problems.append(f"line {index}: amounts must not be negative")
            if debit > ZERO and credit > ZERO:
                problems.append(f"line {index}: a line is a debit or a credit, not both")
            if debit == ZERO and credit == ZERO:
                problems.append(f"line {index}: a line must carry an amount")
            quantized.append(
                ResolvedLine(
                    account=line.account,
                    debit=debit,
                    credit=credit,
                    description=line.description,
                )
            )
        if problems:
            raise ValidationFailed(
                "The posting lines are not valid", details={"problems": problems}
            )

        total_debit = sum((line.debit for line in quantized), ZERO)
        total_credit = sum((line.credit for line in quantized), ZERO)
        if total_debit != total_credit:
            raise UnbalancedEntry(
                "Debits and credits must be equal",
                details={
                    "total_debit": str(total_debit),
                    "total_credit": str(total_credit),
                    "difference": str(total_debit - total_credit),
                },
            )
        if total_debit == ZERO:
            raise UnbalancedEntry("A journal entry must move a non-zero amount")
        return quantized

    # ---------------------------------------------------------------- create

    async def post(
        self,
        *,
        organization_id: uuid.UUID,
        request: PostingRequest,
        actor_user_id: uuid.UUID | None = None,
        on_duplicate: Literal["raise", "return_existing"] = "raise",
    ) -> JournalEntry:
        """Validate and post an entry in one transaction.

        `on_duplicate="return_existing"` makes the call safely retryable: a
        caller that cannot tell whether its previous attempt committed can
        repeat it and receive the same entry rather than a second one.
        """
        period = await self.resolve_period(organization_id=organization_id, on=request.entry_date)
        if period.status is not PeriodStatus.OPEN:
            raise PeriodClosed(
                f"Accounting period {period.code} is closed and cannot accept postings",
                details={"period": period.code, "entry_date": request.entry_date.isoformat()},
            )

        resolved = await self._resolve_accounts(
            organization_id=organization_id, lines=request.lines
        )
        quantized = self._validate_amounts(resolved, request.currency)

        existing = await self._find_existing_event(organization_id=organization_id, request=request)
        if existing is not None:
            return await self._handle_duplicate(existing, on_duplicate)

        entry = await self._write_entry(
            organization_id=organization_id,
            request=request,
            period=period,
            lines=quantized,
            status=EntryStatus.POSTED,
            actor_user_id=actor_user_id,
        )

        event = PostingEvent(
            organization_id=organization_id,
            source_kind=request.source_kind,
            source_id=request.source_id or str(entry.id),
            purpose=request.purpose,
            journal_entry_id=entry.id,
            created_by_user_id=actor_user_id,
        )
        self._session.add(event)
        try:
            # Surfaces a concurrent duplicate: two transactions both passed the
            # check above, and only one of these inserts can succeed.
            await self._session.flush()
        except IntegrityError as exc:
            if "uq_posting_event_source" not in str(exc.orig):
                raise
            await self._session.rollback()
            raise DuplicatePostingEvent(
                "This business event has already been posted",
                details={
                    "source_kind": request.source_kind.value,
                    "source_id": request.source_id,
                    "purpose": request.purpose.value,
                },
            ) from exc

        self._audit_posted(entry, quantized, request)
        return entry

    async def _find_existing_event(
        self, *, organization_id: uuid.UUID, request: PostingRequest
    ) -> PostingEvent | None:
        if request.source_id is None:
            # Without a source id there is nothing to be idempotent about; the
            # unique constraint still protects anything that supplies one.
            return None
        stmt = select(PostingEvent).where(
            PostingEvent.organization_id == organization_id,
            PostingEvent.source_kind == request.source_kind,
            PostingEvent.source_id == request.source_id,
            PostingEvent.purpose == request.purpose,
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def _handle_duplicate(self, event: PostingEvent, on_duplicate: str) -> JournalEntry:
        if on_duplicate == "return_existing":
            entry = await self._session.get(JournalEntry, event.journal_entry_id)
            if entry is None:  # pragma: no cover - FK guarantees presence
                raise NotFound("The previously posted entry is missing")
            log.info(
                "accounting.posting.idempotent_replay",
                entry_number=entry.entry_number,
                source_kind=event.source_kind.value,
                source_id=event.source_id,
            )
            return entry
        raise DuplicatePostingEvent(
            "This business event has already been posted",
            details={
                "source_kind": event.source_kind.value,
                "source_id": event.source_id,
                "purpose": event.purpose.value,
                "journal_entry_id": str(event.journal_entry_id),
            },
        )

    async def _write_entry(
        self,
        *,
        organization_id: uuid.UUID,
        request: PostingRequest,
        period: AccountingPeriod,
        lines: list[ResolvedLine],
        status: EntryStatus,
        actor_user_id: uuid.UUID | None,
        reversal_of_entry_id: uuid.UUID | None = None,
    ) -> JournalEntry:
        now = datetime.now(UTC)
        entry = JournalEntry(
            organization_id=organization_id,
            entry_number=await next_number(self._session, organization_id=organization_id),
            entry_date=request.entry_date,
            period_id=period.id,
            status=status,
            description=request.description,
            currency=request.currency.upper(),
            source_kind=request.source_kind,
            source_id=request.source_id,
            # Stored, not looked up later: editing the policy must never
            # restate what this entry says (ADR-011).
            policy_id=request.policy_id,
            operation_code=request.operation_code,
            # Set at creation, never afterwards: the immutability trigger
            # protects this column so a posted reversal cannot later be
            # re-pointed at a different original.
            reversal_of_entry_id=reversal_of_entry_id,
            reason=request.reason,
            posted_at=now if status is EntryStatus.POSTED else None,
            posted_by_user_id=actor_user_id if status is EntryStatus.POSTED else None,
            created_by_user_id=actor_user_id,
        )
        self._session.add(entry)
        await self._session.flush()

        for number, line in enumerate(lines, start=1):
            self._session.add(
                JournalLine(
                    organization_id=organization_id,
                    journal_entry_id=entry.id,
                    line_number=number,
                    account_id=line.account.id,
                    account_is_postable=True,
                    debit=line.debit,
                    credit=line.credit,
                    description=line.description,
                    entry_date=request.entry_date,
                    period_id=period.id,
                    entry_status=status,
                )
            )
        await self._session.flush()
        return entry

    def _audit_posted(
        self,
        entry: JournalEntry,
        lines: list[ResolvedLine],
        request: PostingRequest,
    ) -> None:
        total = sum((line.debit for line in lines), ZERO)
        self._audit.record(
            events.JOURNAL_ENTRY_POSTED,
            organization_id=entry.organization_id,
            target_type="journal_entry",
            target_id=entry.id,
            target_label=entry.entry_number,
            business_event=f"{request.source_kind.value}:{request.source_id or entry.id}",
            reason=request.reason,
            new_values={
                "entry_number": entry.entry_number,
                "entry_date": entry.entry_date,
                "total": total,
                "currency": entry.currency,
                "line_count": len(lines),
                "operation_code": request.operation_code,
                "accounts": [line.account.code for line in lines],
                **request.audit_context,
            },
        )

    # ----------------------------------------------------------------- draft

    async def create_draft(
        self,
        *,
        organization_id: uuid.UUID,
        request: PostingRequest,
        actor_user_id: uuid.UUID | None = None,
    ) -> JournalEntry:
        """Create an unposted entry. Drafts affect no balance and may be edited."""
        period = await self.resolve_period(organization_id=organization_id, on=request.entry_date)
        resolved = await self._resolve_accounts(
            organization_id=organization_id, lines=request.lines
        )
        # A draft is validated too: catching an unbalanced entry at entry time
        # is more useful than catching it at posting time.
        quantized = self._validate_amounts(resolved, request.currency)
        entry = await self._write_entry(
            organization_id=organization_id,
            request=request,
            period=period,
            lines=quantized,
            status=EntryStatus.DRAFT,
            actor_user_id=actor_user_id,
        )
        self._audit.record(
            events.JOURNAL_ENTRY_CREATED,
            organization_id=organization_id,
            target_type="journal_entry",
            target_id=entry.id,
            target_label=entry.entry_number,
            new_values={"status": "DRAFT", "description": request.description},
        )
        return entry

    async def post_draft(
        self,
        *,
        organization_id: uuid.UUID,
        entry_id: uuid.UUID,
        actor_user_id: uuid.UUID | None = None,
    ) -> JournalEntry:
        """Promote a draft to posted, re-checking everything first."""
        entry = await self.get_entry(organization_id=organization_id, entry_id=entry_id)
        if entry.status is EntryStatus.POSTED:
            raise EntryImmutable(
                f"Entry {entry.entry_number} is already posted",
                details={"entry_number": entry.entry_number},
            )

        period = await self._session.get(AccountingPeriod, entry.period_id)
        if period is None or period.status is not PeriodStatus.OPEN:
            raise PeriodClosed(
                "The entry's accounting period is closed and cannot accept postings",
                details={"entry_number": entry.entry_number},
            )

        lines = await self.get_lines(organization_id=organization_id, entry_id=entry_id)
        total_debit = sum((line.debit for line in lines), ZERO)
        total_credit = sum((line.credit for line in lines), ZERO)
        if len(lines) < MINIMUM_LINES or total_debit != total_credit:
            raise UnbalancedEntry(
                "The draft does not balance and cannot be posted",
                details={
                    "total_debit": str(total_debit),
                    "total_credit": str(total_credit),
                },
            )

        now = datetime.now(UTC)
        entry.status = EntryStatus.POSTED
        entry.posted_at = now
        entry.posted_by_user_id = actor_user_id
        await self._session.flush()

        # The lines' `entry_status` is cascaded by a database trigger, not
        # here. Updating them from Python as well produced a second UPDATE
        # that arrived after the cascade had already run, which the
        # immutability guard correctly rejected. One mechanism owns it; the
        # loaded objects are expired so later reads see the new value.
        for line in lines:
            self._session.expire(line)

        self._audit.record(
            events.JOURNAL_ENTRY_POSTED,
            organization_id=organization_id,
            target_type="journal_entry",
            target_id=entry.id,
            target_label=entry.entry_number,
            business_event=f"{entry.source_kind.value}:{entry.source_id or entry.id}",
            old_values={"status": "DRAFT"},
            new_values={
                "status": "POSTED",
                "total": total_debit,
                "currency": entry.currency,
            },
        )
        return entry

    # -------------------------------------------------------------- reversal

    async def reverse(
        self,
        *,
        organization_id: uuid.UUID,
        entry_id: uuid.UUID,
        reason: str,
        actor_user_id: uuid.UUID | None = None,
        reversal_date: date | None = None,
    ) -> JournalEntry:
        """Post the offsetting entry for a posted one.

        The original is never edited (invariants 4 and 5). The reversal is
        dated today by default, because the original's period may be closed —
        and back-dating a correction into a reported period is a decision for
        an accountant, not a default.
        """
        if not reason or not reason.strip():
            raise ValidationFailed("A reversal requires a reason", details={"field": "reason"})

        original = await self.get_entry(organization_id=organization_id, entry_id=entry_id)
        if original.status is not EntryStatus.POSTED:
            raise Conflict(
                "Only a posted entry can be reversed. Delete the draft instead.",
                details={"entry_number": original.entry_number},
            )
        if original.reversed_by_entry_id is not None:
            raise Conflict(
                f"Entry {original.entry_number} has already been reversed",
                details={
                    "entry_number": original.entry_number,
                    "reversed_by": str(original.reversed_by_entry_id),
                },
            )

        lines = await self.get_lines(organization_id=organization_id, entry_id=entry_id)
        accounts = {
            account.id: account
            for account in (
                await self._session.execute(
                    select(Account).where(
                        Account.organization_id == organization_id,
                        Account.id.in_([line.account_id for line in lines]),
                    )
                )
            ).scalars()
        }
        # Debits and credits exchange places; the amounts are unchanged.
        reversing_lines = [
            ResolvedLine(
                account=accounts[line.account_id],
                debit=line.credit,
                credit=line.debit,
                description=line.description,
            )
            for line in lines
        ]

        on = reversal_date or date.today()
        period = await self.resolve_period(organization_id=organization_id, on=on)
        if period.status is not PeriodStatus.OPEN:
            raise PeriodClosed(
                f"Accounting period {period.code} is closed, so the reversal cannot be dated "
                f"{on.isoformat()}",
                details={"period": period.code},
            )

        request = PostingRequest(
            entry_date=on,
            description=f"Reversal of {original.entry_number}: {original.description}",
            source_kind=original.source_kind,
            source_id=original.source_id,
            purpose=PostingPurpose.REVERSAL,
            lines=[],
            currency=original.currency,
            operation_code=original.operation_code,
            policy_id=original.policy_id,
            reason=reason.strip(),
        )
        reversal = await self._write_entry(
            organization_id=organization_id,
            request=request,
            period=period,
            lines=reversing_lines,
            status=EntryStatus.POSTED,
            actor_user_id=actor_user_id,
            reversal_of_entry_id=original.id,
        )
        # `reversed_by_entry_id` is the one column the immutability trigger
        # lets a posted entry change, which is how the original records that
        # it has been reversed without being edited in any other way.
        original.reversed_by_entry_id = reversal.id

        self._session.add(
            PostingEvent(
                organization_id=organization_id,
                source_kind=original.source_kind,
                source_id=str(original.id),
                purpose=PostingPurpose.REVERSAL,
                journal_entry_id=reversal.id,
                created_by_user_id=actor_user_id,
            )
        )
        await self._session.flush()

        self._audit.record(
            events.JOURNAL_ENTRY_REVERSED,
            organization_id=organization_id,
            target_type="journal_entry",
            target_id=original.id,
            target_label=original.entry_number,
            business_event=f"reversal:{reversal.entry_number}",
            reason=reason.strip(),
            old_values={"reversed_by_entry_id": None},
            new_values={
                "reversed_by_entry_id": str(reversal.id),
                "reversal_entry_number": reversal.entry_number,
                "reversal_date": on,
            },
        )
        return reversal

    # ----------------------------------------------------------------- reads

    async def get_entry(self, *, organization_id: uuid.UUID, entry_id: uuid.UUID) -> JournalEntry:
        stmt = select(JournalEntry).where(
            JournalEntry.organization_id == organization_id,
            JournalEntry.id == entry_id,
        )
        entry = (await self._session.execute(stmt)).scalar_one_or_none()
        if entry is None:
            raise NotFound("Journal entry not found")
        return entry

    async def get_lines(
        self, *, organization_id: uuid.UUID, entry_id: uuid.UUID
    ) -> list[JournalLine]:
        stmt = (
            select(JournalLine)
            .where(
                JournalLine.organization_id == organization_id,
                JournalLine.journal_entry_id == entry_id,
            )
            .order_by(JournalLine.line_number)
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def find_by_source(
        self,
        *,
        organization_id: uuid.UUID,
        source_kind: SourceKind,
        source_id: str,
        purpose: PostingPurpose | None = None,
    ) -> list[JournalEntry]:
        """Every entry produced by one business event.

        How a caller answers "did my posting go through?" without guessing.
        """
        stmt = select(PostingEvent).where(
            PostingEvent.organization_id == organization_id,
            PostingEvent.source_kind == source_kind,
            PostingEvent.source_id == source_id,
        )
        if purpose is not None:
            stmt = stmt.where(PostingEvent.purpose == purpose)
        entry_ids = [e.journal_entry_id for e in (await self._session.execute(stmt)).scalars()]
        if not entry_ids:
            return []
        stmt2 = (
            select(JournalEntry)
            .where(
                JournalEntry.organization_id == organization_id,
                JournalEntry.id.in_(entry_ids),
            )
            .order_by(JournalEntry.entry_number)
        )
        return list((await self._session.execute(stmt2)).scalars().all())

    async def resolve_policy_rule(
        self, *, organization_id: uuid.UUID, policy_id: uuid.UUID, operation_code: str
    ) -> PolicyRule:
        """The Dr/Cr mapping for an operation, from one policy version."""
        stmt = select(PolicyRule).where(
            PolicyRule.organization_id == organization_id,
            PolicyRule.policy_id == policy_id,
            PolicyRule.operation_code == operation_code,
        )
        rule = (await self._session.execute(stmt)).scalar_one_or_none()
        if rule is None:
            raise NotFound(
                f"No rule for operation {operation_code!r} in this policy version",
                details={"operation_code": operation_code},
            )
        return rule
