"""What a stored document is, and what it is about.

Two enumerations, and the reason they are enumerations rather than free text
is the same in both cases: a document people cannot find is a document that
was never filed. A category typed by hand becomes `Shartnoma`, `shartnoma`
and `Contract` in the same drawer within a month.

**Nothing here decides retention.** How long a document must be kept, and
who may destroy it, is `OPEN_QUESTIONS.md` D1 and is unanswered, so this
module archives and never deletes. See `services/storage.py`.
"""

from __future__ import annotations

from enum import StrEnum


class DocumentCategory(StrEnum):
    """What kind of paper this is.

    The list is deliberately short and about *this* business: a clinic files
    contracts, bills, bank paper, identity documents and clinical scans. A
    category that does not fit goes to `OTHER` with a description, which is
    a signal to add one rather than a place to hide.
    """

    CONTRACT = "CONTRACT"
    """An agreement: employment, supplier, lease, patient consent."""

    INVOICE = "INVOICE"
    """A bill issued or received, including a `schyot-faktura`."""

    RECEIPT = "RECEIPT"
    """Proof that money moved: a fiscal receipt, a payment order, a slip."""

    BANK_STATEMENT = "BANK_STATEMENT"
    """A statement or export as the bank produced it."""

    IDENTITY = "IDENTITY"
    """A passport, licence or registration certificate."""

    CLINICAL = "CLINICAL"
    """An X-ray, a scan, a referral, a signed treatment plan."""

    PAYROLL = "PAYROLL"
    """A payslip, an order, a timesheet somebody signed."""

    STATUTORY = "STATUTORY"
    """A filed return, a tax certificate, an inspection report."""

    OTHER = "OTHER"
    """Anything else. Carries a description, and is a prompt to add a category."""


class DocumentEntityType(StrEnum):
    """The business record a document is filed against.

    A document may be filed against nothing at all — the clinic's own
    registration certificate belongs to the organization, not to a patient.
    That is why the link is nullable rather than why this enumeration has an
    `ORGANIZATION` member: a link to the organization would be a link every
    row carries and none of them means anything by.

    Each member names a table that already exists. Adding one without the
    table is how a dangling reference is born, so `ATTACHABLE_TABLES` below
    is checked in the same change.
    """

    PATIENT = "PATIENT"
    EMPLOYEE = "EMPLOYEE"
    INVOICE = "INVOICE"
    SUPPLIER = "SUPPLIER"
    SUPPLIER_INVOICE = "SUPPLIER_INVOICE"
    PURCHASE_ORDER = "PURCHASE_ORDER"
    PAYMENT = "PAYMENT"
    PAYROLL_RUN = "PAYROLL_RUN"
    BANK_ACCOUNT = "BANK_ACCOUNT"
    JOURNAL_ENTRY = "JOURNAL_ENTRY"
    TREATMENT_PLAN = "TREATMENT_PLAN"


#: Entity type → the table its identifier must exist in, within the same
#: organization. The service checks this before storing anything, so a
#: document cannot be filed against a record that is not there or belongs to
#: somebody else. There is no database foreign key, because one column cannot
#: reference eleven tables; this map is the constraint, enforced in one place.
ATTACHABLE_TABLES: dict[DocumentEntityType, str] = {
    DocumentEntityType.PATIENT: "patient",
    DocumentEntityType.EMPLOYEE: "employee",
    DocumentEntityType.INVOICE: "invoice",
    DocumentEntityType.SUPPLIER: "supplier",
    DocumentEntityType.SUPPLIER_INVOICE: "supplier_invoice",
    DocumentEntityType.PURCHASE_ORDER: "purchase_order",
    DocumentEntityType.PAYMENT: "payment",
    DocumentEntityType.PAYROLL_RUN: "payroll_run",
    DocumentEntityType.BANK_ACCOUNT: "bank_account",
    DocumentEntityType.JOURNAL_ENTRY: "journal_entry",
    DocumentEntityType.TREATMENT_PLAN: "treatment_plan",
}


class DocumentStatus(StrEnum):
    """Whether a document is current.

    There is no `DELETED`. A document is archived with a reason and keeps its
    bytes, because the retention policy that would say when destruction is
    permitted does not exist yet (`OPEN_QUESTIONS.md` D1).
    """

    STORED = "STORED"
    """Filed and current."""

    ARCHIVED = "ARCHIVED"
    """Withdrawn from the working list, with a reason. Bytes are kept."""

    SUPERSEDED = "SUPERSEDED"
    """Replaced by a newer document, which names this one."""


#: Statuses that keep a document out of the default list.
HIDDEN_STATUSES: frozenset[DocumentStatus] = frozenset(
    {DocumentStatus.ARCHIVED, DocumentStatus.SUPERSEDED}
)
