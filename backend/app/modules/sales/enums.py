"""Enumerations for sales and receivables."""

from __future__ import annotations

from enum import StrEnum


class CustomerKind(StrEnum):
    """Who is being billed.

    A clinic bills individuals; the same engine bills companies elsewhere.
    Kept separate from the clinical patient record, which lives in the clinic
    module and carries different access rules.
    """

    PERSON = "PERSON"
    COMPANY = "COMPANY"


class InvoiceStatus(StrEnum):
    DRAFT = "DRAFT"
    """Prepared but not issued. No receivable, no revenue, no ledger effect."""

    ISSUED = "ISSUED"
    """Issued to the customer. Revenue is recognised and a receivable exists."""

    PARTIALLY_PAID = "PARTIALLY_PAID"
    PAID = "PAID"

    CANCELLED = "CANCELLED"
    """A draft withdrawn before issue. Never posted, so nothing to reverse."""

    VOID = "VOID"
    """An issued invoice reversed out. The original stays visible."""


#: Statuses in which an invoice is a real receivable.
OPEN_INVOICE_STATUSES = frozenset({InvoiceStatus.ISSUED, InvoiceStatus.PARTIALLY_PAID})

#: Statuses whose invoice has been posted to the ledger.
POSTED_INVOICE_STATUSES = frozenset(
    {
        InvoiceStatus.ISSUED,
        InvoiceStatus.PARTIALLY_PAID,
        InvoiceStatus.PAID,
        InvoiceStatus.VOID,
    }
)


class PaymentMethod(StrEnum):
    CASH = "CASH"
    CARD = "CARD"
    BANK_TRANSFER = "BANK_TRANSFER"


class PaymentStatus(StrEnum):
    RECORDED = "RECORDED"
    """Taken and posted. May be wholly or partly unallocated."""

    REFUNDED = "REFUNDED"
    """Reversed out in full. The original stays visible."""


class ServiceCategory(StrEnum):
    """Broad grouping for the catalogue.

    Deliberately coarse: the statutory ИКПУ (MXIK) code on each service is
    what carries the classification that matters for reporting.
    """

    TREATMENT = "TREATMENT"
    DIAGNOSTIC = "DIAGNOSTIC"
    CONSULTATION = "CONSULTATION"
    MATERIAL = "MATERIAL"
    OTHER = "OTHER"


class RevenueTaxTreatment(StrEnum):
    """How an invoice's revenue is split for tax.

    **Unconfirmed — see `docs/OPEN_QUESTIONS.md` question A5.** The clinic's
    fiscal receipts all report VAT of zero, which is what `NONE` represents,
    while its 2025 bank statement shows VAT being paid. Until an accountant
    resolves that, an organization must state its treatment explicitly and
    the system refuses to guess.
    """

    UNDECLARED = "UNDECLARED"
    """No treatment chosen. Invoices can be prepared but not issued."""

    NONE = "NONE"
    """Revenue is posted gross. Matches every fiscal receipt on file."""

    VAT_INCLUSIVE = "VAT_INCLUSIVE"
    """The price includes VAT; it is extracted at the configured rate."""
