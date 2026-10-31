"""The permission catalogue and the default role definitions.

This registry is the single source of truth. Migrations seed the `permission`
and `role` tables from it, the API enforces against it, and the frontend reads
the resolved set for the signed-in membership in order to decide what to show.
The frontend is never the boundary — it only mirrors what the backend already
decided.

Naming convention: ``<module>.<object>.<action>``.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Permission:
    code: str
    module: str
    description: str
    #: True when the permission authorises a financially material or
    #: privacy-sensitive action. Every use of one of these is audited.
    sensitive: bool = False


def _p(code: str, module: str, description: str, sensitive: bool = False) -> Permission:
    return Permission(code=code, module=module, description=description, sensitive=sensitive)


PERMISSIONS: tuple[Permission, ...] = (
    # --- Identity and access ------------------------------------------------
    _p("identity.user.read", "identity", "View users and their memberships"),
    _p("identity.user.manage", "identity", "Invite, deactivate and edit users", True),
    _p("identity.role.read", "identity", "View roles and permissions"),
    _p("identity.role.manage", "identity", "Grant and revoke roles", True),
    # --- Organization -------------------------------------------------------
    _p("organization.read", "organization", "View organization profile"),
    _p("organization.manage", "organization", "Edit organization profile", True),
    _p("organization.period.read", "organization", "View fiscal years and periods"),
    _p("organization.period.manage", "organization", "Open, close and reopen periods", True),
    # --- Accounting ---------------------------------------------------------
    _p("accounting.account.read", "accounting", "View the chart of accounts"),
    _p("accounting.account.manage", "accounting", "Create and deactivate accounts", True),
    _p("accounting.journal.read", "accounting", "View journal entries"),
    _p("accounting.journal.draft", "accounting", "Create and edit draft journal entries"),
    _p("accounting.journal.post", "accounting", "Post journal entries to the ledger", True),
    _p("accounting.journal.reverse", "accounting", "Reverse a posted journal entry", True),
    _p("accounting.policy.read", "accounting", "View accounting policies"),
    _p("accounting.policy.manage", "accounting", "Edit and activate accounting policies", True),
    # --- Sales and receivables ---------------------------------------------
    _p("sales.service.read", "sales", "View the service catalogue and prices"),
    _p("sales.service.manage", "sales", "Edit the service catalogue and prices", True),
    _p("sales.invoice.read", "sales", "View invoices and customer balances"),
    _p("sales.invoice.manage", "sales", "Issue and amend invoices"),
    _p("sales.payment.record", "sales", "Record a customer payment", True),
    _p("sales.refund.request", "sales", "Request a refund"),
    _p("sales.refund.approve", "sales", "Approve a refund", True),
    # --- Cash ---------------------------------------------------------------
    _p("cash.register.read", "cash", "View cash registers and sessions"),
    _p("cash.register.manage", "cash", "Create and configure cash registers", True),
    _p("cash.session.operate", "cash", "Open, operate and close a cash session", True),
    _p("cash.count.submit", "cash", "Submit a physical cash count"),
    _p("cash.discrepancy.approve", "cash", "Approve a cash discrepancy", True),
    # --- Banking ------------------------------------------------------------
    _p("banking.account.read", "banking", "View bank accounts and transactions"),
    _p("banking.account.manage", "banking", "Create and configure bank accounts", True),
    _p("banking.statement.import", "banking", "Import a bank statement", True),
    _p("banking.reconcile", "banking", "Match transactions and reconcile", True),
    # --- Purchasing and inventory ------------------------------------------
    _p("purchasing.supplier.read", "purchasing", "View suppliers"),
    _p("purchasing.supplier.manage", "purchasing", "Create and edit suppliers"),
    _p("purchasing.order.read", "purchasing", "View purchase requests and orders"),
    _p("purchasing.order.manage", "purchasing", "Raise purchase requests and orders"),
    _p("purchasing.order.approve", "purchasing", "Approve a purchase order", True),
    _p("purchasing.bill.manage", "purchasing", "Register supplier invoices", True),
    _p("purchasing.payment.record", "purchasing", "Record a supplier payment", True),
    _p("inventory.item.read", "inventory", "View products, materials and stock"),
    _p("inventory.item.manage", "inventory", "Edit products, materials and locations"),
    _p("inventory.receipt.record", "inventory", "Receive goods into stock", True),
    _p("inventory.consumption.record", "inventory", "Record material consumption"),
    _p("inventory.adjustment.approve", "inventory", "Approve a stock adjustment", True),
    # --- HR and payroll -----------------------------------------------------
    _p("hr.employee.read", "hr", "View employee records"),
    _p("hr.employee.manage", "hr", "Create and edit employees and contracts", True),
    _p("hr.attendance.record", "hr", "Record attendance"),
    _p("payroll.run.read", "payroll", "View payroll runs and payslips"),
    _p("payroll.run.calculate", "payroll", "Calculate a payroll run"),
    _p("payroll.run.approve", "payroll", "Approve a payroll run", True),
    _p("payroll.run.post", "payroll", "Post an approved payroll run", True),
    _p("payroll.rules.manage", "payroll", "Edit statutory payroll rule versions", True),
    # --- Expenses -----------------------------------------------------------
    _p("expense.request.read", "expense", "View expense requests"),
    _p("expense.request.submit", "expense", "Submit an expense request"),
    _p("expense.request.approve", "expense", "Approve an expense request", True),
    # --- Clinic -------------------------------------------------------------
    _p("clinic.patient.read", "clinic", "View patient registration details"),
    _p("clinic.patient.manage", "clinic", "Register and edit patients", True),
    _p("clinic.clinical.read", "clinic", "Read clinical treatment records", True),
    _p("clinic.clinical.write", "clinic", "Record clinical treatment details", True),
    _p("clinic.appointment.read", "clinic", "View the appointment schedule"),
    _p("clinic.appointment.manage", "clinic", "Book and change appointments"),
    _p("clinic.treatment.complete", "clinic", "Mark a service as completed", True),
    # --- Reporting and documents -------------------------------------------
    _p("reporting.financial.read", "reporting", "Run financial reports"),
    _p("reporting.management.read", "reporting", "Run management dashboards"),
    _p("document.read", "document", "View stored documents"),
    _p("document.manage", "document", "Upload and attach documents"),
    # --- Audit --------------------------------------------------------------
    _p("audit.log.read", "audit", "Read the audit trail", True),
)

PERMISSIONS_BY_CODE: dict[str, Permission] = {p.code: p for p in PERMISSIONS}
ALL_PERMISSION_CODES: frozenset[str] = frozenset(PERMISSIONS_BY_CODE)
SENSITIVE_PERMISSION_CODES: frozenset[str] = frozenset(p.code for p in PERMISSIONS if p.sensitive)


@dataclass(frozen=True, slots=True)
class RoleDefinition:
    code: str
    name_uz: str
    name_ru: str
    name_en: str
    description: str
    permissions: frozenset[str]


def _codes(*prefixes_or_codes: str) -> frozenset[str]:
    """Expand exact codes and ``module.*`` wildcards into a concrete set."""
    resolved: set[str] = set()
    for item in prefixes_or_codes:
        if item.endswith(".*"):
            prefix = item[:-1]
            matched = {c for c in ALL_PERMISSION_CODES if c.startswith(prefix)}
            if not matched:
                raise ValueError(f"Wildcard {item!r} matched no permission")
            resolved |= matched
        else:
            if item not in ALL_PERMISSION_CODES:
                raise ValueError(f"Unknown permission {item!r}")
            resolved.add(item)
    return frozenset(resolved)


#: Segregation of duties: a single role must not hold both permissions in any
#: of these pairs. Enforced when roles are edited and when they are granted.
SEGREGATION_OF_DUTIES: tuple[tuple[str, str, str], ...] = (
    (
        "sales.refund.request",
        "sales.refund.approve",
        "The person who requests a refund must not approve it",
    ),
    (
        "purchasing.order.manage",
        "purchasing.order.approve",
        "The person who raises a purchase order must not approve it",
    ),
    (
        "expense.request.submit",
        "expense.request.approve",
        "The person who submits an expense must not approve it",
    ),
    (
        "payroll.run.calculate",
        "payroll.run.approve",
        "The person who calculates payroll must not approve it",
    ),
    (
        "cash.session.operate",
        "cash.discrepancy.approve",
        "A cashier must not approve their own cash discrepancy",
    ),
)

#: Read-only roles may hold no write permission at all.
READ_ONLY_ROLE_CODES: frozenset[str] = frozenset({"auditor"})

SYSTEM_ROLES: tuple[RoleDefinition, ...] = (
    RoleDefinition(
        code="owner",
        name_uz="Ташкилот эгаси",
        name_ru="Владелец организации",
        name_en="Organization Owner",
        description="Full authority inside one organization, including approvals.",
        permissions=ALL_PERMISSION_CODES,
    ),
    RoleDefinition(
        code="director",
        name_uz="Директор",
        name_ru="Директор",
        name_en="Director",
        description="Operational leadership and approvals; cannot edit accounting policy.",
        permissions=_codes(
            "organization.read",
            "organization.period.read",
            "identity.user.read",
            "identity.role.read",
            "accounting.account.read",
            "accounting.journal.read",
            "accounting.policy.read",
            "sales.*",
            "cash.register.read",
            "cash.count.submit",
            "cash.discrepancy.approve",
            "banking.account.read",
            # A director approves purchasing; raising the order is somebody
            # else's job, so that the approval stays an independent control.
            "purchasing.supplier.read",
            "purchasing.order.read",
            "purchasing.order.approve",
            "inventory.item.read",
            "inventory.adjustment.approve",
            "hr.employee.read",
            "payroll.run.read",
            "payroll.run.approve",
            "expense.request.read",
            "expense.request.approve",
            "clinic.patient.read",
            "clinic.appointment.read",
            "reporting.*",
            "document.*",
            "audit.log.read",
        )
        - {"sales.refund.request"},
    ),
    RoleDefinition(
        code="accountant",
        name_uz="Бухгалтер",
        name_ru="Бухгалтер",
        name_en="Accountant",
        description="Keeps the ledger: posts entries, reconciles, runs statutory reports.",
        permissions=_codes(
            "organization.read",
            "organization.period.read",
            "organization.period.manage",
            "accounting.account.read",
            "accounting.account.manage",
            "accounting.journal.read",
            "accounting.journal.draft",
            "accounting.journal.post",
            "accounting.journal.reverse",
            "accounting.policy.read",
            "sales.service.read",
            "sales.invoice.read",
            "sales.invoice.manage",
            "sales.payment.record",
            "cash.register.read",
            "banking.*",
            "purchasing.supplier.read",
            "purchasing.supplier.manage",
            "purchasing.order.read",
            "purchasing.bill.manage",
            "purchasing.payment.record",
            "inventory.item.read",
            "hr.employee.read",
            "payroll.run.read",
            "payroll.run.calculate",
            "payroll.run.post",
            "expense.request.read",
            "reporting.financial.read",
            "document.*",
            "audit.log.read",
        ),
    ),
    RoleDefinition(
        code="cashier",
        name_uz="Кассир",
        name_ru="Кассир",
        name_en="Cashier",
        description="Takes payments and runs the cash register. No accounting authority.",
        permissions=_codes(
            "organization.read",
            "sales.service.read",
            "sales.invoice.read",
            "sales.invoice.manage",
            "sales.payment.record",
            "sales.refund.request",
            "cash.register.read",
            "cash.session.operate",
            "cash.count.submit",
            "clinic.patient.read",
            "clinic.appointment.read",
            "document.read",
        ),
    ),
    RoleDefinition(
        code="receptionist",
        name_uz="Қабулхона ходими",
        name_ru="Администратор",
        name_en="Receptionist",
        description="Registers patients and manages the schedule. No clinical record access.",
        permissions=_codes(
            "organization.read",
            "clinic.patient.read",
            "clinic.patient.manage",
            "clinic.appointment.read",
            "clinic.appointment.manage",
            "sales.service.read",
            "sales.invoice.read",
            "document.read",
        ),
    ),
    RoleDefinition(
        code="dentist",
        name_uz="Стоматолог",
        name_ru="Стоматолог",
        name_en="Dentist",
        description="Treats patients and records clinical details for their own patients.",
        permissions=_codes(
            "organization.read",
            "clinic.patient.read",
            "clinic.clinical.read",
            "clinic.clinical.write",
            "clinic.appointment.read",
            "clinic.appointment.manage",
            "clinic.treatment.complete",
            "sales.service.read",
            "inventory.item.read",
            "inventory.consumption.record",
            "document.read",
        ),
    ),
    RoleDefinition(
        code="inventory_manager",
        name_uz="Омбор бошқарувчиси",
        name_ru="Менеджер склада",
        name_en="Inventory Manager",
        description="Runs stock: receiving, consumption and adjustments.",
        permissions=_codes(
            "organization.read",
            "inventory.*",
            "purchasing.supplier.read",
            "purchasing.order.read",
            "purchasing.order.manage",
            "document.read",
            "document.manage",
        )
        - {"inventory.adjustment.approve"},
    ),
    RoleDefinition(
        code="hr_payroll_manager",
        name_uz="Кадрлар ва иш ҳақи бошқарувчиси",
        name_ru="Менеджер по кадрам и зарплате",
        name_en="HR / Payroll Manager",
        description="Maintains employees and prepares payroll for approval.",
        permissions=_codes(
            "organization.read",
            "hr.*",
            "payroll.run.read",
            "payroll.run.calculate",
            "document.read",
            "document.manage",
        ),
    ),
    RoleDefinition(
        code="auditor",
        name_uz="Аудитор",
        name_ru="Аудитор",
        name_en="Auditor",
        description="Read-only access to financial records and the audit trail.",
        permissions=_codes(
            "organization.read",
            "organization.period.read",
            "identity.user.read",
            "identity.role.read",
            "accounting.account.read",
            "accounting.journal.read",
            "accounting.policy.read",
            "sales.service.read",
            "sales.invoice.read",
            "cash.register.read",
            "banking.account.read",
            "purchasing.supplier.read",
            "purchasing.order.read",
            "inventory.item.read",
            "hr.employee.read",
            "payroll.run.read",
            "expense.request.read",
            "reporting.*",
            "document.read",
            "audit.log.read",
        ),
    ),
)

SYSTEM_ROLES_BY_CODE: dict[str, RoleDefinition] = {r.code: r for r in SYSTEM_ROLES}

#: Permissions that are, by convention, writes. Used to validate read-only roles.
_WRITE_SUFFIXES = (
    ".manage",
    ".post",
    ".reverse",
    ".draft",
    ".record",
    ".approve",
    ".submit",
    ".request",
    ".operate",
    ".import",
    ".complete",
    ".calculate",
    ".write",
)


def is_write_permission(code: str) -> bool:
    return code.endswith(_WRITE_SUFFIXES) or code == "banking.reconcile"


def check_role_permission_set(role_code: str, permission_codes: set[str]) -> list[str]:
    """Return the reasons `permission_codes` may not be held by one role.

    An empty list means the combination is acceptable. The owner role is
    exempt from segregation of duties: a single-owner MChJ has nobody else to
    delegate approval to, and the audit trail is what makes that reviewable.
    """
    problems: list[str] = []
    unknown = sorted(permission_codes - ALL_PERMISSION_CODES)
    if unknown:
        problems.append(f"Unknown permissions: {', '.join(unknown)}")

    if role_code in READ_ONLY_ROLE_CODES:
        writes = sorted(c for c in permission_codes if is_write_permission(c))
        if writes:
            problems.append(
                f"Role {role_code!r} is read-only but was given write permissions: "
                f"{', '.join(writes)}"
            )

    if role_code != "owner":
        for left, right, reason in SEGREGATION_OF_DUTIES:
            if left in permission_codes and right in permission_codes:
                problems.append(f"{reason} ({left} + {right})")
    return problems
