"""Response shapes for the management reports.

Amounts are decimals serialised as strings, like every other money in this
API: a JavaScript number cannot hold a som figure exactly, and a report is
the last place to start rounding.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from pydantic import BaseModel

from app.core.schemas import MoneyMixin
from app.modules.accounting.enums import AccountElement


class StatementLineResponse(BaseModel, MoneyMixin):
    account_id: uuid.UUID
    account_code: str
    account_name: str
    element: AccountElement | None
    amount: Decimal
    debit: Decimal
    credit: Decimal
    #: True while this account's statutory classification is unconfirmed
    #: (`OPEN_QUESTIONS.md` A1). The screen marks these rather than hiding
    #: them, because a silent classification is the thing to avoid.
    needs_confirmation: bool


class StatementSectionResponse(BaseModel, MoneyMixin):
    key: str
    lines: list[StatementLineResponse]
    total: Decimal


class ProfitAndLossResponse(BaseModel, MoneyMixin):
    from_date: date
    to_date: date
    currency: str
    revenue: StatementSectionResponse
    expenses: StatementSectionResponse
    #: Accounts carrying a balance that the chart has not classified. Never
    #: folded into revenue or expenses to make the statement look tidy.
    unclassified: StatementSectionResponse
    net_result: Decimal
    accounts_needing_confirmation: int
    comparative: ProfitAndLossResponse | None = None


class BalanceSheetResponse(BaseModel, MoneyMixin):
    as_of: date
    currency: str
    assets: StatementSectionResponse
    liabilities: StatementSectionResponse
    equity: StatementSectionResponse
    unclassified: StatementSectionResponse
    #: Revenue less expenses for the open fiscal year, not yet closed to
    #: equity. Carried separately because nobody has posted it there yet.
    result_for_the_year: Decimal
    total_assets: Decimal
    total_liabilities_and_equity: Decimal
    difference: Decimal
    #: False means the statement does not balance, which the screen says out
    #: loud rather than hiding behind a rounded total.
    balances: bool
    accounts_needing_confirmation: int
    fiscal_year_starts_on: date | None
    comparative: BalanceSheetResponse | None = None


class CashAccountRowResponse(BaseModel, MoneyMixin):
    account_id: uuid.UUID
    account_code: str
    account_name: str
    opening: Decimal
    movement: Decimal
    closing: Decimal


class CashFlowLineResponse(BaseModel, MoneyMixin):
    account_id: uuid.UUID
    account_code: str
    account_name: str
    element: AccountElement | None
    amount: Decimal


class CashFlowResponse(BaseModel, MoneyMixin):
    from_date: date
    to_date: date
    currency: str
    opening_balance: Decimal
    closing_balance: Decimal
    total_in: Decimal
    total_out: Decimal
    net_movement: Decimal
    difference: Decimal
    #: Opening plus movement equals closing. False means the statement is
    #: wrong and says so.
    reconciles: bool
    cash_accounts: list[CashAccountRowResponse]
    inflows: list[CashFlowLineResponse]
    outflows: list[CashFlowLineResponse]
    #: True when no till and no bank account have been set up, so there is
    #: nothing this report could be about.
    no_cash_accounts_configured: bool


class PayablesAgeingRowResponse(BaseModel, MoneyMixin):
    supplier_id: uuid.UUID
    supplier_name: str
    current: Decimal
    days_1_30: Decimal
    days_31_60: Decimal
    days_61_90: Decimal
    days_over_90: Decimal
    total: Decimal


class PayablesAgeingResponse(BaseModel, MoneyMixin):
    as_of: date
    currency: str
    rows: list[PayablesAgeingRowResponse]
    total_outstanding: Decimal
    #: True when the clinic has no suppliers yet, so the report is empty for
    #: a reason worth stating rather than looking broken.
    no_suppliers_yet: bool


class ReportCapability(BaseModel):
    """One report, and whether it can actually be run.

    This is what the Reports screen renders instead of a hand-written list.
    A report that is blocked says which question blocks it, so nobody has to
    read the source to find out why a button is missing.
    """

    key: str
    available: bool
    #: The open question that blocks it, where one does: "A2", "A3", "A5".
    blocked_by: str | None = None
    #: True for statutory filing forms, which stay blocked until an
    #: accountant confirms the formats and the account mappings.
    statutory: bool = False


class ReportCatalogueResponse(BaseModel):
    """Every report this build can produce, answered by the backend.

    The module's own statement of what works, so the screen never has to
    guess and never shows a module-not-developed message for something that
    does work.
    """

    reports: list[ReportCapability]
    accounts_needing_confirmation: int
    #: True when the chart's statutory classification is still unconfirmed,
    #: which every statement footnotes (`OPEN_QUESTIONS.md` A1).
    chart_unconfirmed: bool
