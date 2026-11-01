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
        status=ModuleStatus.PARTIAL,
        available_features=("organization summary", "module implementation status"),
        missing_features=("financial KPIs", "cash and revenue charts"),
    ),
    ModuleInfo(
        key="clinic",
        phase=3,
        # PARTIAL, and the split matters: the money side of a patient's visit
        # is built and tested end to end, while the clinical side exists as
        # services and routes with no screen behind them. A dentist cannot
        # yet record a tooth from a browser, and saying so is more use than
        # a status that implies they can.
        status=ModuleStatus.PARTIAL,
        required_permission="clinic.patient.read",
        available_features=(
            "patient registration with duplicate review",
            "duplicate review queue: compare two records and decide",
            "patient account: debt, advances and bills",
            "payment: cash, card, advances, with a derived remaining balance",
            "drafting an invoice from completed treatment",
        ),
        missing_features=(
            "appointment scheduling screen",
            "clinical record screen: encounters, teeth, findings",
            "treatment plan screen",
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
            "daily close posting shortage or overage",
        ),
        missing_features=(
            "giving a cash payment back has no screen yet",
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
        status=ModuleStatus.PLANNED,
        required_permission="purchasing.order.read",
        missing_features=("purchase orders", "goods receiving", "supplier invoices", "payables"),
    ),
    ModuleInfo(
        key="inventory",
        phase=4,
        status=ModuleStatus.PLANNED,
        required_permission="inventory.item.read",
        missing_features=("stock movements", "valuation", "batch and expiry tracking"),
    ),
    ModuleInfo(
        key="employees",
        phase=5,
        status=ModuleStatus.PLANNED,
        required_permission="hr.employee.read",
        missing_features=("employee records", "contracts", "attendance"),
    ),
    ModuleInfo(
        key="payroll",
        phase=5,
        status=ModuleStatus.PLANNED,
        required_permission="payroll.run.read",
        missing_features=("compensation rules", "payroll runs", "approval", "payslips"),
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
        status=ModuleStatus.PARTIAL,
        required_permission="reporting.financial.read",
        available_features=("trial balance", "account statement"),
        missing_features=(
            "balance sheet",
            "profit and loss",
            "cash flow statement",
            "receivables and payables ageing",
        ),
    ),
    ModuleInfo(
        key="documents",
        phase=7,
        status=ModuleStatus.PLANNED,
        required_permission="document.read",
        missing_features=("document upload", "attachment to business records"),
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
