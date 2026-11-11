"""What is actually built, declared in one place.

The frontend reads this to decide whether a navigation entry opens a working
screen or a clearly-labelled "not available yet" page. Nothing else is allowed
to decide that, which is how the interface stays honest about its own state:
a module is `AVAILABLE` only once it meets the project's definition of done.

Update this registry in the same change that completes a module — never ahead
of it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class ModuleStatus(StrEnum):
    AVAILABLE = "available"
    """Backend, frontend, authorization, audit and tests are all in place."""

    PARTIAL = "partial"
    """Some workflows work; the module page says which ones do not."""

    PLANNED = "planned"
    """Nothing is implemented. The UI shows an explicit placeholder."""


@dataclass(frozen=True, slots=True)
class ModuleInfo:
    key: str
    #: Phase from the implementation plan that delivers this module.
    phase: int
    status: ModuleStatus
    #: Permission a user needs before the navigation entry is shown at all.
    required_permission: str | None = None
    #: Workflows that are live. Shown on a PARTIAL module's page.
    available_features: tuple[str, ...] = field(default_factory=tuple)
    #: Workflows deliberately not built yet, so the UI can say so.
    missing_features: tuple[str, ...] = field(default_factory=tuple)


MODULES: tuple[ModuleInfo, ...] = (
    ModuleInfo(
        key="dashboard",
        phase=1,
        # Still PARTIAL, and for a narrower reason than before: the day's
        # figures are real and derived on the server, but there is no view of
        # a trend — "is this week better than last" is a question the screen
        # cannot answer yet. The technical module list moved to
        # Settings → System status, where an administrator looks for it.
        status=ModuleStatus.PARTIAL,
        available_features=(
            "today's takings, split by cash and card",
            "patient debt and advances held, agreeing with the ageing report",
            "cash session status, including a till left open from an earlier day",
            "appointments, patients registered, treatment unfinished and unbilled",
            "quick actions, offered only where the person may act and a screen exists",
            "every figure withheld rather than zeroed where the person may not read it",
        ),
        missing_features=(
            "trends: this week against last, and revenue over time",
            "expenses and bank balance (purchasing and reporting, phases 4 and 6)",
        ),
    ),
    ModuleInfo(
        key="clinic",
        phase=3,
        # PARTIAL. The materials gap that kept it here is now closed —
        # a dentist records what was used on a tooth and it comes off the
        # shelf — so what is left is recall and reminders, which nobody has
        # specified yet. Nothing is marked available on a plan to build it.
        status=ModuleStatus.PARTIAL,
        required_permission="clinic.patient.read",
        available_features=(
            "patient registration with duplicate review",
            "duplicate review queue: compare two records and decide",
            "patient account: debt, advances and bills",
            "clinical record: visits, per-tooth work in FDI notation, findings",
            "the appointment book, refusing two bookings in one chair",
            "completing treatment, and cancelling it with a reason",
            "correcting a recorded treatment, keeping the original and the reason",
            "courses of treatment across several visits, with their own life cycle",
            "drafting an invoice from completed treatment, once per item",
            "materials used on a tooth, taken off the shelf as they are recorded",
            "payment: cash, card, advances, with a derived remaining balance",
        ),
        missing_features=(
            "what a material cost, on the treatment that used it "
            "(the valuation method is undeclared — OPEN_QUESTIONS I1)",
            "recall and reminders for a patient due back",
        ),
    ),
    ModuleInfo(
        key="sales",
        phase=3,
        # PARTIAL: billing works end to end, but a revenue posting's tax
        # split is still unanswered (OPEN_QUESTIONS A5), so an invoice can
        # only be issued under the "no tax split" treatment.
        status=ModuleStatus.PARTIAL,
        required_permission="sales.invoice.read",
        available_features=(
            "customers and service catalogue with versioned prices",
            "invoices: draft, issue, void",
            "payments: cash, card and transfer, with allocation",
            "advances held as a liability until applied",
            "refunds by reversal",
            "receivables ageing",
        ),
        missing_features=(
            "VAT-inclusive pricing (rate unconfirmed — see OPEN_QUESTIONS A5)",
            "fiscal receipt generation and import",
            "credit notes",
            "statements sent to customers",
        ),
    ),
    ModuleInfo(
        key="cash",
        phase=3,
        status=ModuleStatus.PARTIAL,
        required_permission="cash.register.read",
        available_features=(
            "cash registers",
            "sessions with an opening float",
            "cash payments taken into the drawer",
            "cash expenses and bank deposits via staff or collection",
            "physical count with an explained, approved discrepancy",
            "giving back part of a payment the patient has not spent",
            "daily close posting shortage or overage",
        ),
        missing_features=(
            "fiscal receipt generation and import",
            "cash transfers between registers",
            "multi-currency tills",
        ),
    ),
    ModuleInfo(
        key="banking",
        phase=3,
        # PARTIAL: importing and matching work, but the bank's real export
        # format is unconfirmed (OPEN_QUESTIONS B3), so the column mapping
        # ships with the headings observed in the clinic's own workbook and
        # may need configuring per account on the first real statement.
        status=ModuleStatus.PARTIAL,
        required_permission="banking.account.read",
        available_features=(
            "bank accounts paired with their ledger account",
            "statement import from CSV and Excel, with a per-account column mapping",
            "duplicate lines refused on re-import",
            "one-to-one, one-to-many and many-to-one matching",
            "card settlement with the withheld commission split out",
            "cash deposits confirmed against their transit account",
            "lines set aside with a reason, and reopened",
        ),
        missing_features=(
            "the bank's real export format (unconfirmed — see OPEN_QUESTIONS B3)",
            "a bank charge on anything but a card settlement (OPEN_QUESTIONS B4)",
            "outgoing lines: supplier and tax payments arrive in Phase 4",
            "direct bank integration",
        ),
    ),
    ModuleInfo(
        key="purchases",
        phase=4,
        # PARTIAL: the four states are built and posted correctly, but goods
        # received before their invoice are not accrued — the workbook has no
        # goods-received-not-invoiced account and one is not invented here.
        # The gap is real, bounded, and recorded as an open question.
        status=ModuleStatus.PARTIAL,
        required_permission="purchasing.order.read",
        available_features=(
            "suppliers, with a duplicate tax number refused",
            "purchase requests, approved by somebody other than the asker",
            "purchase orders, which post nothing until an invoice arrives",
            "deliveries recorded against an order, part or whole",
            "what has arrived and not yet been invoiced",
            "supplier invoices: draft, registered into the books, voided",
            "one supplier's invoice number recorded once, so a delivery is "
            "not paid for twice",
            "partial payments, with what is left derived from live allocations",
            "advances paid before an invoice, applied when one arrives",
            "a cash payment that moves the till as well as the ledger",
            "payables ageing, from the due date",
            "a delivery of a stocked material puts it on the shelf",
        ),
        missing_features=(
            "goods received but not yet invoiced are not accrued "
            "(no approved account — see OPEN_QUESTIONS P5)",
            "what the stock on the shelf is worth: derivable from these bills, "
            "but only once the valuation method is declared (OPEN_QUESTIONS I1)",
            "matching a supplier payment on a bank statement (phase 3d, "
            "outgoing lines)",
        ),
    ),
    ModuleInfo(
        key="inventory",
        phase=4,
        # PARTIAL, and the reason is the one this module was always going to
        # have: stock is counted but not costed. What a box of composite is
        # *worth* on the shelf is OPEN_QUESTIONS I1 and nobody has answered
        # it, so the organization declares its valuation method and the
        # system refuses to invent one. Quantities, low stock and expiry all
        # work without an answer; value waits for it.
        status=ModuleStatus.PARTIAL,
        required_permission="inventory.item.read",
        available_features=(
            "materials, categories, units and the shelves they are kept on",
            "stock as a ledger of movements: a balance is always a sum",
            "receiving: a delivery puts stock on the shelf, through purchasing",
            "material used on a treatment, recorded against the tooth",
            "batches and expiry dates, with what is about to go out of date",
            "low stock, for the items somebody set a level on",
            "a physical count, approved by somebody who did not count it",
            "writing off broken or expired stock, with a reason, audited",
            "transfers between shelves, as two movements that balance",
            "refusing to take more off a shelf than is on it",
        ),
        missing_features=(
            "what stock is worth: the valuation method is undeclared "
            "(OPEN_QUESTIONS I1), so quantities are kept and value is refused",
            "posting the cost of a material as it is used — same answer, same question",
            "first-in-first-out costing, which would need cost layers this system "
            "does not keep",
        ),
    ),
    ModuleInfo(
        key="employees",
        phase=5,
        # PARTIAL, not AVAILABLE, for one reason worth naming: the clinic's
        # legal timesheet uses twelve single-letter statutory codes whose
        # Uzbek wording is unconfirmed (OPEN_QUESTIONS P1). The module's own
        # five codes describe what happens in a three-person clinic and are
        # labelled as its own; mapping them onto a form somebody signs needs
        # the real wording, and is not guessed.
        status=ModuleStatus.PARTIAL,
        required_permission="hr.employee.read",
        available_features=(
            "employee records, kept and marked rather than deleted",
            "positions, separate from the roles that grant authority",
            "linking an employee to their login, where they have one",
            "one person holding several roles, read from their membership",
            "the ordinary week, as a rota",
            "a monthly timesheet: worked, absent, sick, leave, holiday",
            "ending an employment, with a date and a reason, always audited",
        ),
        missing_features=(
            "the statutory timesheet's twelve codes (wording unconfirmed — see OPEN_QUESTIONS P1)",
            "contracts and the documents behind them (documents, phase 7)",
            "what anybody is paid (payroll, phase 5)",
        ),
    ),
    ModuleInfo(
        key="payroll",
        phase=5,
        # PARTIAL, and for a reason the clinic can act on rather than wait
        # for: the calculation, the approval chain and the posting are all
        # built and tested, but a run cannot be approved or posted until an
        # accountant approves the statutory rates it needs. The social tax
        # rate could not be verified during the workbook audit
        # (OPEN_QUESTIONS P2), so nothing is seeded with it and every run is
        # blocked on it today. That is the control working.
        status=ModuleStatus.PARTIAL,
        required_permission="payroll.run.read",
        available_features=(
            "statutory rates as dated, approved rules with their legal reference",
            "salary agreements per employee, a rise being a new row",
            "salary prorated by days actually worked, from the timesheet",
            "employee deductions and employer contributions kept apart",
            "bonuses and agreed deductions, each requiring a reason",
            "calculating, approving, posting and paying as four separate acts",
            "the person who calculated a run cannot approve it",
            "payslips that keep their own amounts, rates and rule versions",
            "one accounting entry per run, from the clinic's own posting rules",
        ),
        missing_features=(
            "posting is blocked until the statutory rates are approved — the "
            "social tax rate is unconfirmed (OPEN_QUESTIONS P2)",
            "2018's progressive income tax, whose calculation is missing from "
            "the workbook entirely (OPEN_QUESTIONS P10)",
            "percentage and commission pay, which needs P3 answered",
            "the statutory payroll reports and certificates",
        ),
    ),
    ModuleInfo(
        key="accounting",
        phase=2,
        # PARTIAL, not AVAILABLE: the engine, its invariants and its screens
        # are built and tested, but the seeded chart's statutory
        # classification is still unconfirmed (OPEN_QUESTIONS A1).
        status=ModuleStatus.PARTIAL,
        required_permission="accounting.journal.read",
        available_features=(
            "chart of accounts (statutory import, marked unconfirmed)",
            "fiscal years and monthly accounting periods",
            "journal entries: draft, post, reverse",
            "period close and audited reopen",
            "trial balance and account statements",
            "accounting policy: versions, rules, activation and supersession",
        ),
        missing_features=(
            "balance sheet and profit and loss",
            "cash flow statement",
            "opening balance import",
        ),
    ),
    ModuleInfo(
        key="reports",
        phase=6,
        # PARTIAL, and the reason is now a narrow one. Every *management*
        # report a clinic runs is built and reconciles to the ledger. What is
        # missing is the statutory filing forms, which stay blocked until an
        # accountant confirms the formats and their account mappings
        # (OPEN_QUESTIONS A2 and A3), and anything VAT-dependent (A5).
        # `GET /reporting/catalogue` serves this same answer to the screen,
        # so the two can never drift apart.
        status=ModuleStatus.PARTIAL,
        required_permission="reporting.financial.read",
        available_features=(
            "profit and loss, with a comparative period",
            "balance sheet, which says whether it balanced",
            "patient receivables, by how overdue they are",
            "supplier payables, by how overdue they are",
            "cash movement: what came in, what went out, and what for",
            "trial balance",
            "account statement, reached by drilling into any report line",
            "every report as a spreadsheet, and as a printable page",
        ),
        missing_features=(
            "statutory financial statement forms — the formats and their line "
            "mappings are unconfirmed (OPEN_QUESTIONS A2)",
            "the statutory cash flow statement: direct or indirect is "
            "unanswered (OPEN_QUESTIONS A3), so movements are reported "
            "without that classification",
            "a VAT return, which depends on whether dental services bear VAT "
            "at all (OPEN_QUESTIONS A5)",
        ),
    ),
    ModuleInfo(
        key="documents",
        phase=7,
        # PARTIAL, and the remaining gap is a policy one rather than a
        # technical one: files are stored, checksummed, filed against the
        # record they are about and verifiable after a restore, but nothing
        # here destroys anything, because how long a clinic must keep a
        # signed contract is OPEN_QUESTIONS D1 and nobody has answered it.
        # A system that quietly deleted evidence on a guessed schedule would
        # be worse than one that keeps too much.
        status=ModuleStatus.PARTIAL,
        required_permission="document.read",
        available_features=(
            "uploading a document, with its type and contents checked against "
            "an allowlist",
            "filing one against the record it is about: a patient, an "
            "employee, a bill, a supplier, a payment, a payroll run",
            "a SHA-256 taken at upload and checked again on every download",
            "download refused, and recorded, when the stored bytes no longer "
            "match that checksum",
            "verifying the whole store against the database, which is how a "
            "restored backup is proved rather than hoped about",
            "replacing a document, the older one superseded and kept",
            "archiving with a reason; nothing is destroyed",
            "who downloaded which document, and when",
        ),
        missing_features=(
            "deleting anything at all: the retention and destruction policy "
            "is undecided (OPEN_QUESTIONS D1), so documents are archived and "
            "kept",
            "scanning uploads for malware, which needs a scanner this "
            "deployment does not have",
            "full-text search inside a document's contents",
        ),
    ),
    ModuleInfo(
        key="settings",
        phase=1,
        status=ModuleStatus.PARTIAL,
        required_permission="organization.read",
        available_features=(
            "organization profile",
            "users and memberships",
            "roles and permissions",
        ),
        missing_features=("accounting policy configuration", "numbering sequences"),
    ),
)

MODULES_BY_KEY: dict[str, ModuleInfo] = {m.key: m for m in MODULES}


def module_status(key: str) -> ModuleStatus:
    info = MODULES_BY_KEY.get(key)
    return info.status if info else ModuleStatus.PLANNED
