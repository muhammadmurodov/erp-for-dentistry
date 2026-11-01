"""Fixtures for the settlement tests.

The worked example throughout is the specification's: a treatment priced at
1 000 000 UZS, settled in cash, by card, by both, or in part.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
import pytest_asyncio

from app.modules.sales.enums import RevenueTaxTreatment
from app.modules.sales.services.billing import InvoiceLineInput

#: The specification's worked example, in so'm.
TREATMENT = Decimal("1000000")
ISSUE_DATE = date(2026, 10, 1)
YEAR_START = date(2026, 1, 1)
YEAR_END = date(2026, 12, 31)


@pytest_asyncio.fixture
async def bill(sales, billing):
    """Factory for an issued invoice: `await bill()` or `await bill("500000")`.

    Issued, not drafted — a draft carries no receivable, so there is nothing
    to settle against it.
    """

    async def _bill(amount: Decimal | str = TREATMENT, issue_date: date = ISSUE_DATE):
        invoice = await billing.create_invoice(
            organization_id=sales.id,
            customer_id=sales.customer.id,
            issue_date=issue_date,
            lines=[
                InvoiceLineInput(
                    description="Canal treatment",
                    service_id=sales.service.id,
                    unit_price=Decimal(amount),
                )
            ],
        )
        return await billing.issue_invoice(
            organization_id=sales.id,
            invoice_id=invoice.id,
            tax_treatment=RevenueTaxTreatment.NONE,
        )

    return _bill


@pytest_asyncio.fixture
async def open_till(cash, till, sales):
    """An open cash session, because cash without a drawer is a phantom."""
    return await cash.open_session(
        organization_id=sales.id,
        register_id=till.id,
        business_date=ISSUE_DATE,
        opening_float=Decimal("0"),
    )


@pytest_asyncio.fixture
async def advance(billing, sales):
    """Factory for money the patient left with nothing yet to apply it to."""
    from app.modules.sales.enums import PaymentMethod

    async def _advance(amount: Decimal | str):
        return await billing.record_payment(
            organization_id=sales.id,
            customer_id=sales.customer.id,
            amount=Decimal(amount),
            method=PaymentMethod.CARD,
            received_on=ISSUE_DATE,
        )

    return _advance


@pytest.fixture
def key():
    """Distinct idempotency keys, so a test opts in to replay deliberately."""
    counter = iter(range(1, 1000))
    return lambda: f"test-submission-{next(counter):04d}"
