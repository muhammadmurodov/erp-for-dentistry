"""Reports as a spreadsheet, because that is what gets emailed to an accountant.

**The export is the same figures, not a second computation.** Every writer
here takes the report object the API already returned and lays it out; none
of them queries anything. A spreadsheet that recomputed its own totals would
be a second version of the facts, which is the thing this project exists not
to do.

**Why there is no PDF writer here.** A PDF needs a layout engine, and the
two candidates are a large native dependency or a headless browser — both
heavier than the problem. The reports screen has a print stylesheet instead,
so "Save as PDF" in the browser produces a clean page with the clinic's name
and the period on it. That is a real answer rather than a missing feature,
and it is written down as the decision it is (ADR-059).
"""

from __future__ import annotations

import io
from datetime import date
from decimal import Decimal
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, Side
from openpyxl.utils import get_column_letter

HEADER = Font(bold=True, size=11)
TITLE = Font(bold=True, size=14)
TOTAL = Font(bold=True)
MUTED = Font(italic=True, size=9)
UNDERLINE = Border(top=Side(style="thin"))

#: Uzbek sums have no minor unit in practice but the ledger keeps two, so the
#: format shows what is there without inventing precision.
MONEY_FORMAT = "#,##0.00"


class SheetBuilder:
    """A small layout helper, so every report sheet looks the same."""

    def __init__(self, workbook: Workbook, title: str) -> None:
        self.sheet = workbook.active
        self.sheet.title = title[:31]
        self._row = 1

    def heading(self, text: str) -> None:
        cell = self.sheet.cell(row=self._row, column=1, value=text)
        cell.font = TITLE
        self._row += 1

    def meta(self, label: str, value: str) -> None:
        self.sheet.cell(row=self._row, column=1, value=label)
        self.sheet.cell(row=self._row, column=2, value=value)
        self._row += 1

    def note(self, text: str) -> None:
        cell = self.sheet.cell(row=self._row, column=1, value=text)
        cell.font = MUTED
        self._row += 1

    def blank(self) -> None:
        self._row += 1

    def header_row(self, values: list[str]) -> None:
        for column, value in enumerate(values, start=1):
            cell = self.sheet.cell(row=self._row, column=column, value=value)
            cell.font = HEADER
            cell.alignment = Alignment(horizontal="right" if column > 2 else "left")
        self._row += 1

    def row(self, values: list[Any], *, bold: bool = False, rule: bool = False) -> None:
        for column, value in enumerate(values, start=1):
            cell = self.sheet.cell(row=self._row, column=column, value=value)
            if isinstance(value, Decimal):
                cell.number_format = MONEY_FORMAT
            if bold:
                cell.font = TOTAL
            if rule:
                cell.border = UNDERLINE
        self._row += 1

    def autosize(self, widths: list[int]) -> None:
        for index, width in enumerate(widths, start=1):
            self.sheet.column_dimensions[get_column_letter(index)].width = width


def _workbook() -> Workbook:
    workbook = Workbook()
    return workbook


def _finish(workbook: Workbook) -> bytes:
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _period(from_date: date, to_date: date) -> str:
    return f"{from_date.isoformat()} — {to_date.isoformat()}"


def profit_and_loss_xlsx(report: Any, *, organization_name: str) -> bytes:
    workbook = _workbook()
    builder = SheetBuilder(workbook, "Profit and loss")
    builder.heading(organization_name)
    builder.meta("Report", "Profit and loss (management)")
    builder.meta("Period", _period(report.from_date, report.to_date))
    builder.meta("Currency", report.currency)
    if report.comparative is not None:
        builder.meta(
            "Compared with",
            _period(report.comparative.from_date, report.comparative.to_date),
        )
    builder.note(
        "Management report. Not a statutory filing form — those wait on "
        "OPEN_QUESTIONS A2."
    )
    if report.accounts_needing_confirmation:
        builder.note(
            f"{report.accounts_needing_confirmation} account(s) carry an unconfirmed "
            "statutory classification (OPEN_QUESTIONS A1)."
        )
    builder.blank()

    comparative = report.comparative
    columns = ["Account", "Name", "Amount"]
    if comparative is not None:
        columns += ["Comparative", "Change"]
    builder.header_row(columns)

    for key, section in (
        ("Revenue", report.revenue),
        ("Expenses", report.expenses),
        ("Unclassified", report.unclassified),
    ):
        if not section.lines:
            continue
        builder.row([key], bold=True)
        prior = _by_code(getattr(comparative, section.key, None)) if comparative else {}
        for line in section.lines:
            values: list[Any] = [line.account_code, line.account_name, line.amount]
            if comparative is not None:
                was = prior.get(line.account_code, Decimal("0"))
                values += [was, line.amount - was]
            builder.row(values)
        total_values: list[Any] = ["", f"Total {key.lower()}", section.total]
        if comparative is not None:
            prior_total = getattr(comparative, section.key).total
            total_values += [prior_total, section.total - prior_total]
        builder.row(total_values, bold=True, rule=True)
        builder.blank()

    result_values: list[Any] = ["", "Net result", report.net_result]
    if comparative is not None:
        result_values += [comparative.net_result, report.net_result - comparative.net_result]
    builder.row(result_values, bold=True, rule=True)
    builder.autosize([14, 46, 18, 18, 18])
    return _finish(workbook)


def balance_sheet_xlsx(report: Any, *, organization_name: str) -> bytes:
    workbook = _workbook()
    builder = SheetBuilder(workbook, "Balance sheet")
    builder.heading(organization_name)
    builder.meta("Report", "Balance sheet (management)")
    builder.meta("As at", report.as_of.isoformat())
    builder.meta("Currency", report.currency)
    if report.comparative is not None:
        builder.meta("Compared with", report.comparative.as_of.isoformat())
    builder.note(
        "Management report. Not a statutory filing form — those wait on "
        "OPEN_QUESTIONS A2."
    )
    if not report.balances:
        builder.note(f"THIS STATEMENT DOES NOT BALANCE. Difference: {report.difference}")
    if report.accounts_needing_confirmation:
        builder.note(
            f"{report.accounts_needing_confirmation} account(s) carry an unconfirmed "
            "statutory classification (OPEN_QUESTIONS A1)."
        )
    builder.blank()

    comparative = report.comparative
    columns = ["Account", "Name", "Amount"]
    if comparative is not None:
        columns += ["Comparative", "Change"]
    builder.header_row(columns)

    for key, section in (
        ("Assets", report.assets),
        ("Liabilities", report.liabilities),
        ("Equity", report.equity),
        ("Unclassified", report.unclassified),
    ):
        if not section.lines:
            continue
        builder.row([key], bold=True)
        prior = _by_code(getattr(comparative, section.key, None)) if comparative else {}
        for line in section.lines:
            values: list[Any] = [line.account_code, line.account_name, line.amount]
            if comparative is not None:
                was = prior.get(line.account_code, Decimal("0"))
                values += [was, line.amount - was]
            builder.row(values)
        total_values: list[Any] = ["", f"Total {key.lower()}", section.total]
        if comparative is not None:
            prior_total = getattr(comparative, section.key).total
            total_values += [prior_total, section.total - prior_total]
        builder.row(total_values, bold=True, rule=True)
        builder.blank()

    builder.row(["", "Result for the year (not yet closed)", report.result_for_the_year], bold=True)
    builder.row(["", "Total assets", report.total_assets], bold=True)
    builder.row(
        ["", "Total liabilities and equity", report.total_liabilities_and_equity], bold=True
    )
    builder.row(["", "Difference", report.difference], bold=True, rule=True)
    builder.autosize([14, 46, 18, 18, 18])
    return _finish(workbook)


def cash_flow_xlsx(report: Any, *, organization_name: str) -> bytes:
    workbook = _workbook()
    builder = SheetBuilder(workbook, "Cash movement")
    builder.heading(organization_name)
    builder.meta("Report", "Cash movement (management)")
    builder.meta("Period", _period(report.from_date, report.to_date))
    builder.meta("Currency", report.currency)
    builder.note(
        "Movements are not split into operating, investing and financing: "
        "which method the filing requires is OPEN_QUESTIONS A3, unanswered."
    )
    if not report.reconciles:
        builder.note(f"THIS STATEMENT DOES NOT RECONCILE. Difference: {report.difference}")
    builder.blank()

    builder.header_row(["Account", "Name", "Opening", "Movement", "Closing"])
    for row in report.cash_accounts:
        builder.row([row.account_code, row.account_name, row.opening, row.movement, row.closing])
    builder.row(
        [
            "",
            "Cash in hand and at bank",
            report.opening_balance,
            report.net_movement,
            report.closing_balance,
        ],
        bold=True,
        rule=True,
    )
    builder.blank()

    builder.header_row(["Account", "Name", "In"])
    for line in report.inflows:
        builder.row([line.account_code, line.account_name, line.amount])
    builder.row(["", "Total received", report.total_in], bold=True, rule=True)
    builder.blank()

    builder.header_row(["Account", "Name", "Out"])
    for line in report.outflows:
        builder.row([line.account_code, line.account_name, line.amount])
    builder.row(["", "Total paid", report.total_out], bold=True, rule=True)
    builder.autosize([14, 46, 18, 18, 18])
    return _finish(workbook)


def receivables_xlsx(report: Any, *, organization_name: str) -> bytes:
    workbook = _workbook()
    builder = SheetBuilder(workbook, "Patients owing")
    builder.heading(organization_name)
    builder.meta("Report", "Patient receivables, by age")
    builder.meta("As at", report.as_of.isoformat())
    builder.meta("Currency", report.currency)
    builder.blank()
    buckets = [bucket.label for bucket in report.bucket_totals]
    builder.header_row(["Patient", *buckets, "Total"])
    for customer in report.customers:
        amounts = {bucket.label: bucket.amount for bucket in customer.buckets}
        builder.row(
            [
                customer.customer_name,
                *[amounts.get(label, Decimal("0")) for label in buckets],
                customer.total_outstanding,
            ]
        )
    builder.row(
        ["Total", *[bucket.amount for bucket in report.bucket_totals], report.total_outstanding],
        bold=True,
        rule=True,
    )
    builder.autosize([40, 16, 16, 16, 16, 16, 18])
    return _finish(workbook)


def payables_xlsx(rows: Any, *, organization_name: str, as_of: date, currency: str) -> bytes:
    workbook = _workbook()
    builder = SheetBuilder(workbook, "Owed to suppliers")
    builder.heading(organization_name)
    builder.meta("Report", "Supplier payables, by age")
    builder.meta("As at", as_of.isoformat())
    builder.meta("Currency", currency)
    builder.blank()
    builder.header_row(["Supplier", "Current", "1-30", "31-60", "61-90", "Over 90", "Total"])
    for row in rows:
        builder.row(
            [
                row.supplier_name,
                row.current,
                row.days_1_30,
                row.days_31_60,
                row.days_61_90,
                row.days_over_90,
                row.total,
            ]
        )

    def column_total(attribute: str) -> Decimal:
        return sum((getattr(row, attribute) for row in rows), Decimal("0"))

    builder.row(
        [
            "Total",
            column_total("current"),
            column_total("days_1_30"),
            column_total("days_31_60"),
            column_total("days_61_90"),
            column_total("days_over_90"),
            column_total("total"),
        ],
        bold=True,
        rule=True,
    )
    builder.autosize([40, 16, 16, 16, 16, 16, 18])
    return _finish(workbook)


def _by_code(section: Any) -> dict[str, Decimal]:
    if section is None:
        return {}
    return {line.account_code: line.amount for line in section.lines}
