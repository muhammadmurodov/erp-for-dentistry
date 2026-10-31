"""Request and response shapes for the accounting API.

Monetary fields are serialised as **strings**, not JSON numbers. A JSON number
is a float to most clients, and a large som amount would lose precision on the
way through. The frontend formats the string and never converts it.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.schemas import MoneyMixin
from app.modules.accounting.enums import (
    AccountElement,
    AccountLevel,
    AccountNature,
    EntryStatus,
    FiscalYearStatus,
    PeriodStatus,
    PolicyStatus,
    PostingPurpose,
    SourceKind,
)

# --------------------------------------------------------------- accounts


class AccountResponse(BaseModel, MoneyMixin):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    name: str
    name_uz: str | None
    name_ru: str | None
    level: AccountLevel
    nature: AccountNature | None
    element: AccountElement | None
    statutory_part: str | None
    parent_id: uuid.UUID | None
    is_postable: bool
    is_active: bool
    #: True while the statutory classification is unconfirmed (OPEN_QUESTIONS A1).
    needs_confirmation: bool
    description: str | None


class AccountWithBalance(AccountResponse):
    balance: Decimal


class AccountCreateRequest(BaseModel):
    code: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=255)
    level: AccountLevel = AccountLevel.ACCOUNT
    nature: AccountNature | None = None
    element: AccountElement | None = None
    parent_code: str | None = Field(default=None, max_length=32)
    description: str | None = None


class SeedChartRequest(BaseModel):
    #: Import only the accounts the reference data marks as commonly used by
    #: a small business, plus the structural rows above them.
    only_commonly_used: bool = False
    confirm: bool = Field(description="Must be true: seeding writes 368 accounts and is audited.")

    @field_validator("confirm")
    @classmethod
    def _must_confirm(cls, value: bool) -> bool:
        if not value:
            raise ValueError("Set confirm=true to import the statutory chart")
        return value


class SeedChartResponse(BaseModel):
    accounts: int
    postable: int
    structural: int
    parents_linked: int
    notice: str


# ------------------------------------------------------- periods and years


class FiscalYearResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    starts_on: date
    ends_on: date
    status: FiscalYearStatus


class FiscalYearCreateRequest(BaseModel):
    code: str = Field(min_length=1, max_length=16)
    starts_on: date
    ends_on: date
    create_monthly_periods: bool = True


class PeriodResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    fiscal_year_id: uuid.UUID
    code: str
    starts_on: date
    ends_on: date
    status: PeriodStatus
    closed_at: datetime | None
    reopened_reason: str | None


class ReopenPeriodRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=1000)


# ------------------------------------------------------------- journal


class JournalLineInput(BaseModel, MoneyMixin):
    account_code: str | None = Field(default=None, max_length=32)
    account_id: uuid.UUID | None = None
    debit: Decimal = Decimal("0")
    credit: Decimal = Decimal("0")
    description: str | None = None

    @field_validator("debit", "credit", mode="before")
    @classmethod
    def _no_floats(cls, value: object) -> object:
        # A float here would already have lost precision; refuse it at the
        # edge rather than quantizing a wrong number.
        if isinstance(value, float):
            raise ValueError("Send monetary amounts as strings, not JSON numbers")
        return value


class JournalLineResponse(BaseModel, MoneyMixin):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    line_number: int
    account_id: uuid.UUID
    debit: Decimal
    credit: Decimal
    description: str | None


class JournalLineDetail(JournalLineResponse):
    account_code: str
    account_name: str


class JournalEntryResponse(BaseModel, MoneyMixin):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    entry_number: str
    entry_date: date
    period_id: uuid.UUID
    status: EntryStatus
    description: str
    currency: str
    source_kind: SourceKind
    source_id: str | None
    operation_code: str | None
    reversal_of_entry_id: uuid.UUID | None
    reversed_by_entry_id: uuid.UUID | None
    reason: str | None
    posted_at: datetime | None
    #: Sum of the debits. Not a stored column — computed from the lines and
    #: filled in by the API layer, which is why it carries a default.
    total: Decimal = Decimal("0")


class JournalEntryDetail(JournalEntryResponse):
    lines: list[JournalLineDetail]


class JournalEntryCreateRequest(BaseModel):
    entry_date: date
    description: str = Field(min_length=1, max_length=2000)
    lines: list[JournalLineInput] = Field(min_length=2, max_length=200)
    currency: str = Field(default="UZS", pattern="^[A-Z]{3}$")
    #: A manual entry must say why it exists — accounting invariant 6.
    reason: str | None = Field(default=None, max_length=1000)
    post_immediately: bool = False


class ReverseEntryRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=1000)
    reversal_date: date | None = None


# --------------------------------------------------------------- policies


class PolicyRuleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    operation_code: str
    description: str
    description_uz: str | None
    description_ru: str | None
    debit_account_id: uuid.UUID
    credit_account_id: uuid.UUID
    required_documents: str | None


class PolicyRuleDetail(PolicyRuleResponse):
    debit_account_code: str
    credit_account_code: str


class PolicyResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    name: str
    version: int
    status: PolicyStatus
    effective_from: date
    effective_to: date | None
    activated_at: datetime | None
    notes: str | None
    rule_count: int = 0


class PolicyCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    effective_from: date
    code: str = Field(default="default", max_length=64)
    notes: str | None = None


class PolicyRuleRequest(BaseModel):
    operation_code: str = Field(min_length=1, max_length=64)
    description: str = Field(min_length=1)
    debit_account_code: str = Field(min_length=1, max_length=32)
    credit_account_code: str = Field(min_length=1, max_length=32)
    description_uz: str | None = None
    description_ru: str | None = None
    required_documents: str | None = None


# ---------------------------------------------------------------- reports


class TrialBalanceRowResponse(BaseModel, MoneyMixin):
    account_id: uuid.UUID
    account_code: str
    account_name: str
    nature: AccountNature | None
    element: AccountElement | None
    opening_debit: Decimal
    opening_credit: Decimal
    period_debit: Decimal
    period_credit: Decimal
    closing_debit: Decimal
    closing_credit: Decimal


class TrialBalanceResponse(BaseModel, MoneyMixin):
    from_date: date
    to_date: date
    currency: str
    rows: list[TrialBalanceRowResponse]
    total_opening_debit: Decimal
    total_opening_credit: Decimal
    total_period_debit: Decimal
    total_period_credit: Decimal
    total_closing_debit: Decimal
    total_closing_credit: Decimal
    #: False would mean the ledger was corrupted by something that bypassed
    #: the posting service. The UI shows it rather than assuming it.
    is_balanced: bool


class LedgerLineResponse(BaseModel, MoneyMixin):
    entry_id: uuid.UUID
    entry_number: str
    entry_date: date
    description: str
    line_description: str | None
    debit: Decimal
    credit: Decimal
    running_balance: Decimal
    is_reversal: bool
    reversed: bool


class AccountStatementResponse(BaseModel, MoneyMixin):
    account_id: uuid.UUID
    account_code: str
    account_name: str
    nature: AccountNature | None
    from_date: date
    to_date: date
    opening_balance: Decimal
    closing_balance: Decimal
    total_debit: Decimal
    total_credit: Decimal
    lines: list[LedgerLineResponse]


class PostingPurposeInfo(BaseModel):
    value: PostingPurpose
    label: str
