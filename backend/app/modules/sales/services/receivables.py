"""Accounts receivable ageing.

Derived from issued invoices and their allocation rows at the moment it is
asked for. Nothing is cached: a stored ageing figure is a second version of
the facts, and the two diverge the first time somebody back-dates a payment.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import ZERO, quantize
from app.modules.sales.enums import OPEN_INVOICE_STATUSES
from app.modules.sales.models import Customer, Invoice, PaymentAllocation

#: `(label, from_days, to_days)`. `to_days=None` is the open-ended bucket.
#: Ageing runs from the **due date** where one is set, falling back to the
#: issue date — an invoice is not overdue before it is due.
AGEING_BUCKETS: tuple[tuple[str, int, int | None], ...] = (
    ("current", 0, 0),
    ("1-30", 1, 30),
    ("31-60", 31, 60),
    ("61-90", 61, 90),
    ("90+", 91, None),
)


@dataclass(frozen=True, slots=True)
class Bucket:
    label: str
    from_days: int
    to_days: int | None
    amount: Decimal


@dataclass(frozen=True, slots=True)
class CustomerAgeingRow:
    customer_id: uuid.UUID
    customer_code: str
    customer_name: str
    total_outstanding: Decimal
    buckets: list[Bucket]


@dataclass(frozen=True, slots=True)
class AgeingReport:
    as_of: date
    currency: str
    customers: list[CustomerAgeingRow]
    total_outstanding: Decimal
    bucket_totals: list[Bucket]


class ReceivablesService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @staticmethod
    def _bucket_for(days_overdue: int) -> str:
        for label, start, end in AGEING_BUCKETS:
            if days_overdue <= 0:
                return AGEING_BUCKETS[0][0]
            if days_overdue >= start and (end is None or days_overdue <= end):
                return label
        return AGEING_BUCKETS[-1][0]

    async def ageing(
        self,
        *,
        organization_id: uuid.UUID,
        as_of: date | None = None,
        currency: str = "UZS",
    ) -> AgeingReport:
        as_of = as_of or date.today()

        # One query for the invoices, one for their allocations: the
        # alternative is a per-invoice round trip, which grows with the ledger.
        invoices = list(
            (
                await self._session.execute(
                    select(Invoice, Customer)
                    .join(
                        Customer,
                        (Customer.id == Invoice.customer_id)
                        & (Customer.organization_id == Invoice.organization_id),
                    )
                    .where(
                        Invoice.organization_id == organization_id,
                        Invoice.status.in_(list(OPEN_INVOICE_STATUSES)),
                        Invoice.issue_date <= as_of,
                    )
                    .order_by(Customer.display_name, Invoice.issue_date)
                )
            ).all()
        )
        if not invoices:
            return AgeingReport(
                as_of=as_of,
                currency=currency,
                customers=[],
                total_outstanding=ZERO,
                bucket_totals=[
                    Bucket(label, start, end, ZERO) for label, start, end in AGEING_BUCKETS
                ],
            )

        allocated = {
            invoice_id: Decimal(total)
            for invoice_id, total in (
                await self._session.execute(
                    select(
                        PaymentAllocation.invoice_id,
                        func.coalesce(func.sum(PaymentAllocation.amount), 0),
                    )
                    .where(
                        PaymentAllocation.organization_id == organization_id,
                        PaymentAllocation.invoice_id.in_([inv.id for inv, _ in invoices]),
                    )
                    .group_by(PaymentAllocation.invoice_id)
                )
            ).all()
        }

        per_customer: dict[uuid.UUID, dict] = {}
        bucket_totals = dict.fromkeys((b[0] for b in AGEING_BUCKETS), ZERO)
        grand_total = ZERO

        for invoice, customer in invoices:
            outstanding = quantize(
                invoice.total - allocated.get(invoice.id, ZERO), invoice.currency
            )
            if outstanding <= ZERO:
                continue

            reference = invoice.due_date or invoice.issue_date
            bucket = self._bucket_for((as_of - reference).days)

            entry = per_customer.setdefault(
                customer.id,
                {
                    "code": customer.code,
                    "name": customer.display_name,
                    "total": ZERO,
                    "buckets": dict.fromkeys((b[0] for b in AGEING_BUCKETS), ZERO),
                },
            )
            entry["total"] += outstanding
            entry["buckets"][bucket] += outstanding
            bucket_totals[bucket] += outstanding
            grand_total += outstanding

        rows = [
            CustomerAgeingRow(
                customer_id=customer_id,
                customer_code=data["code"],
                customer_name=data["name"],
                total_outstanding=quantize(data["total"], currency),
                buckets=[
                    Bucket(label, start, end, quantize(data["buckets"][label], currency))
                    for label, start, end in AGEING_BUCKETS
                ],
            )
            for customer_id, data in per_customer.items()
        ]
        rows.sort(key=lambda r: r.total_outstanding, reverse=True)

        return AgeingReport(
            as_of=as_of,
            currency=currency,
            customers=rows,
            total_outstanding=quantize(grand_total, currency),
            bucket_totals=[
                Bucket(label, start, end, quantize(bucket_totals[label], currency))
                for label, start, end in AGEING_BUCKETS
            ],
        )
