"""Banking request and response shapes. Money travels as decimal strings."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.schemas import MoneyMixin
from app.modules.banking.enums import (
    ImportStatus,
    MatchableKind,
    TransactionDirection,
    TransactionStatus,
)


def _reject_float(value: object) -> object:
    if isinstance(value, float):
        raise ValueError("Send monetary amounts as strings, not JSON numbers")
    return value


class BankAccountResponse(BaseModel):
    """The account number is masked: the full value is a payment credential."""

    id: uuid.UUID
    code: str
    name: str
    bank_name: str | None
    mfo: str | None
    account_number_masked: str | None
    account_id: uuid.UUID
    currency: str
    is_active: bool
    has_column_mapping: bool


class BankAccountCreateRequest(BaseModel):
    code: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=255)
    #: The ledger account this bank account's balance belongs to — 5110.01
    #: for the settlement account in the clinic's chart.
    account_code: str = Field(default="5110.01", max_length=32)
    bank_name: str | None = Field(default=None, max_length=255)
    mfo: str | None = Field(default=None, max_length=16)
    account_number: str | None = Field(default=None, max_length=34)
    currency: str = Field(default="UZS", pattern="^[A-Z]{3}$")
    column_mapping: dict[str, str] | None = None


class ColumnMappingRequest(BaseModel):
    """Maps this system's field names to the bank's own column headings.

    Needed because the bank's export format is open question B3. Sending
    `null` returns the account to the built-in aliases.
    """

    column_mapping: dict[str, str] | None = None


class DeactivateBankAccountRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


class ColumnMappingDefaults(BaseModel):
    """What the importer recognises without configuration."""

    aliases: dict[str, list[str]]


class ImportResultResponse(BaseModel, MoneyMixin):
    import_id: uuid.UUID
    rows_read: int
    rows_imported: int
    duplicates_skipped: int
    rows_rejected: int
    problems: list[str]
    period_from: date | None
    period_to: date | None


class StatementImportResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    bank_account_id: uuid.UUID
    filename: str
    status: ImportStatus
    rows_read: int
    rows_imported: int
    duplicates_skipped: int
    rows_rejected: int
    period_from: date | None
    period_to: date | None
    problems: list[str] | None
    created_at: datetime


class BankTransactionResponse(BaseModel, MoneyMixin):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    bank_account_id: uuid.UUID
    value_date: date
    direction: TransactionDirection
    amount: Decimal
    currency: str
    description: str | None
    counterparty_name: str | None
    counterparty_mfo: str | None
    document_number: str | None
    status: TransactionStatus
    ignored_reason: str | None
    commission_amount: Decimal


class BankTransactionDetail(BankTransactionResponse):
    matched_amount: Decimal
    unaccounted_amount: Decimal
    matches: list[MatchResponse]


class MatchResponse(BaseModel, MoneyMixin):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    source_kind: MatchableKind
    source_id: uuid.UUID
    amount: Decimal
    note: str | None
    created_at: datetime


class MatchCandidateResponse(BaseModel, MoneyMixin):
    kind: MatchableKind
    id: uuid.UUID
    label: str
    occurred_on: date
    amount: Decimal
    already_matched: Decimal
    outstanding: Decimal


class MatchItemRequest(BaseModel):
    kind: MatchableKind
    id: uuid.UUID
    amount: Decimal

    @field_validator("amount", mode="before")
    @classmethod
    def _no_float(cls, value: object) -> object:
        return _reject_float(value)


class MatchRequestBody(BaseModel):
    records: list[MatchItemRequest] = Field(min_length=1, max_length=200)
    #: What the bank or acquirer withheld, so the gross takings, the fee and
    #: the net settlement are each recorded rather than inferred.
    commission: Decimal = Field(default=Decimal("0"))
    note: str | None = Field(default=None, max_length=500)

    @field_validator("commission", mode="before")
    @classmethod
    def _no_float(cls, value: object) -> object:
        return _reject_float(value)


class IgnoreTransactionRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


BankTransactionDetail.model_rebuild()
