"""Canonical audit action names.

Keeping these as constants rather than inline strings means a renamed event
breaks at import time instead of quietly splitting one audit history into two.
"""

from __future__ import annotations

from typing import Final

# --- authentication ---------------------------------------------------------
LOGIN_SUCCEEDED: Final = "identity.login.succeeded"
LOGIN_FAILED: Final = "identity.login.failed"
LOGIN_BLOCKED: Final = "identity.login.blocked"
LOGOUT: Final = "identity.logout"
TOKEN_REFRESHED: Final = "identity.token.refreshed"
TOKEN_REPLAY_DETECTED: Final = "identity.token.replay_detected"
PASSWORD_CHANGED: Final = "identity.password.changed"

# --- access control ---------------------------------------------------------
PERMISSION_DENIED: Final = "identity.permission.denied"
USER_CREATED: Final = "identity.user.created"
USER_UPDATED: Final = "identity.user.updated"
USER_DEACTIVATED: Final = "identity.user.deactivated"
MEMBERSHIP_CREATED: Final = "identity.membership.created"
MEMBERSHIP_UPDATED: Final = "identity.membership.updated"
ROLE_GRANTED: Final = "identity.role.granted"
ROLE_REVOKED: Final = "identity.role.revoked"
ROLE_CREATED: Final = "identity.role.created"
ROLE_UPDATED: Final = "identity.role.updated"

# --- organization -----------------------------------------------------------
ORGANIZATION_CREATED: Final = "organization.created"
ORGANIZATION_UPDATED: Final = "organization.updated"
PERIOD_CLOSED: Final = "organization.period.closed"
PERIOD_REOPENED: Final = "organization.period.reopened"

# --- accounting (used from Phase 2) ----------------------------------------
JOURNAL_ENTRY_CREATED: Final = "accounting.journal.created"
JOURNAL_ENTRY_POSTED: Final = "accounting.journal.posted"
JOURNAL_ENTRY_REVERSED: Final = "accounting.journal.reversed"
ACCOUNT_CREATED: Final = "accounting.account.created"
ACCOUNT_DEACTIVATED: Final = "accounting.account.deactivated"
POLICY_CHANGED: Final = "accounting.policy.changed"
POLICY_ACTIVATED: Final = "accounting.policy.activated"
POLICY_DISCARDED: Final = "accounting.policy.discarded"

# --- money movement (Phase 3+) ---------------------------------------------
#: Declaring, or withdrawing, that the organization is too small to separate
#: duties. It weakens an approval control for everyone, so it is recorded
#: whatever else is or is not audited.
DUTY_SEPARATION_DECLARED: Final = "organization.duties.declared"
PAYMENT_RECORDED: Final = "sales.payment.recorded"
PAYMENT_ALLOCATED: Final = "sales.payment.allocated"
#: One submission of the payment form: cash, card, an applied advance, or a
#: mixture, taken against one invoice as a single act.
INVOICE_SETTLED: Final = "sales.invoice.settled"
ALLOCATION_REVERSED: Final = "sales.payment.allocation_reversed"
REFUND_REQUESTED: Final = "sales.refund.requested"
REFUND_APPROVED: Final = "sales.refund.approved"
#: Clearing an uncollectable debt. Separate from voiding an invoice, which
#: says the sale never happened: a write-off leaves the revenue standing.
DEBT_WRITTEN_OFF: Final = "sales.debt.written_off"
CASH_SESSION_OPENED: Final = "cash.session.opened"
CASH_SESSION_CLOSED: Final = "cash.session.closed"
CASH_COUNT_SUBMITTED: Final = "cash.count.submitted"
CASH_DISCREPANCY_APPROVED: Final = "cash.discrepancy.approved"
STATEMENT_IMPORTED: Final = "banking.statement.imported"
RECONCILIATION_MATCHED: Final = "banking.reconciliation.matched"

# --- purchasing (Phase 4) ---------------------------------------------------
SUPPLIER_CREATED: Final = "purchasing.supplier.created"
SUPPLIER_UPDATED: Final = "purchasing.supplier.updated"
PURCHASE_REQUEST_SUBMITTED: Final = "purchasing.request.submitted"
PURCHASE_REQUEST_DECIDED: Final = "purchasing.request.decided"
PURCHASE_ORDER_CREATED: Final = "purchasing.order.created"
#: Committing the clinic to a supplier. Nothing posts, but somebody is now
#: expecting to be paid, so who approved it is always on record.
PURCHASE_ORDER_APPROVED: Final = "purchasing.order.approved"
PURCHASE_ORDER_CLOSED: Final = "purchasing.order.closed"
GOODS_RECEIVED: Final = "purchasing.goods.received"
SUPPLIER_INVOICE_REGISTERED: Final = "purchasing.bill.registered"
SUPPLIER_INVOICE_VOIDED: Final = "purchasing.bill.voided"
SUPPLIER_PAYMENT_RECORDED: Final = "purchasing.payment.recorded"
SUPPLIER_PAYMENT_ALLOCATED: Final = "purchasing.payment.allocated"
SUPPLIER_ALLOCATION_REVERSED: Final = "purchasing.payment.allocation_reversed"

# --- employees (Phase 5) ----------------------------------------------------
EMPLOYEE_CREATED: Final = "hr.employee.created"
EMPLOYEE_UPDATED: Final = "hr.employee.updated"
#: Ending somebody's employment. Financially material — it stops a payroll
#: run paying them — and personally consequential, so it is always recorded.
EMPLOYMENT_ENDED: Final = "hr.employee.employment_ended"
POSITION_CREATED: Final = "hr.position.created"
SCHEDULE_CHANGED: Final = "hr.schedule.changed"
ATTENDANCE_RECORDED: Final = "hr.attendance.recorded"
#: Removing a day recorded by mistake. Its own action rather than an
#: outcome: `outcome` is constrained to success, failure and denied.
ATTENDANCE_REMOVED: Final = "hr.attendance.removed"

# --- inventory and payroll (Phase 4+) --------------------------------------
ITEM_CREATED: Final = "inventory.item.created"
ITEM_UPDATED: Final = "inventory.item.updated"
STOCK_RECEIVED: Final = "inventory.stock.received"
#: Material used on a patient. Recorded by the dentist who used it.
STOCK_CONSUMED: Final = "inventory.stock.consumed"
#: Stock taken off for expiry, breakage or spoiling, always with a reason.
STOCK_WRITTEN_OFF: Final = "inventory.stock.written_off"
STOCK_TRANSFERRED: Final = "inventory.stock.transferred"
#: A physical count, proposed by whoever counted.
STOCK_COUNT_PROPOSED: Final = "inventory.count.proposed"
#: The moment stock actually moves, by somebody who did not count it.
STOCK_ADJUSTED: Final = "inventory.stock.adjusted"
STOCK_COUNT_REJECTED: Final = "inventory.count.rejected"
#: A statutory rate written down. Drafting is free; it calculates nothing.
PAYROLL_RULE_PROPOSED: Final = "payroll.rule.proposed"
#: Somebody with authority accepting a statutory rate as the one to apply.
#: Audited because every payslip computed afterwards depends on it.
PAYROLL_RULE_APPROVED: Final = "payroll.rule.approved"
PAYROLL_RULE_SUPERSEDED: Final = "payroll.rule.superseded"
#: What the clinic agreed to pay one person, from one date.
COMPENSATION_SET: Final = "payroll.compensation.set"
PAYROLL_CALCULATED: Final = "payroll.run.calculated"
PAYROLL_APPROVED: Final = "payroll.run.approved"
PAYROLL_POSTED: Final = "payroll.run.posted"
PAYROLL_PAID: Final = "payroll.run.paid"
PAYROLL_CANCELLED: Final = "payroll.run.cancelled"
#: A bonus or a deduction for one person, which cannot be recorded without
#: a reason — the control the workbook's nine undocumented corrections
#: lacked (WORKBOOK_FORMULA_AUDIT.md, question P8).
PAYROLL_ADJUSTED: Final = "payroll.adjustment.recorded"

# --- clinic (Phase 3c) ------------------------------------------------------
PATIENT_REGISTERED: Final = "clinic.patient.registered"
PATIENT_UPDATED: Final = "clinic.patient.updated"
#: Joining two records into one person. Never automatic, always reasoned.
PATIENT_MERGED: Final = "clinic.patient.merged"
ENCOUNTER_OPENED: Final = "clinic.encounter.opened"
ENCOUNTER_COMPLETED: Final = "clinic.encounter.completed"
TREATMENT_RECORDED: Final = "clinic.treatment.recorded"
TREATMENT_COMPLETED: Final = "clinic.treatment.completed"
TREATMENT_CORRECTED: Final = "clinic.treatment.corrected"
TREATMENT_BILLED: Final = "clinic.treatment.billed"
APPOINTMENT_BOOKED: Final = "clinic.appointment.booked"
APPOINTMENT_CHANGED: Final = "clinic.appointment.changed"
PLAN_CREATED: Final = "clinic.plan.created"
#: Moving a course of treatment between states — proposed, accepted, abandoned.
PLAN_STATUS_CHANGED: Final = "clinic.plan.status_changed"

# --- privacy ----------------------------------------------------------------
CLINICAL_RECORD_READ: Final = "clinic.clinical.read"
DOCUMENT_DOWNLOADED: Final = "document.downloaded"

#: Actions that must always be written, even when the surrounding request
#: failed. Checked by `tests/security/test_audit_coverage.py`.
REQUIRED_AUDIT_ACTIONS: Final[frozenset[str]] = frozenset(
    {
        LOGIN_SUCCEEDED,
        LOGIN_FAILED,
        PERMISSION_DENIED,
        JOURNAL_ENTRY_POSTED,
        JOURNAL_ENTRY_REVERSED,
        POLICY_CHANGED,
        PERIOD_CLOSED,
        PERIOD_REOPENED,
        DUTY_SEPARATION_DECLARED,
        PAYMENT_RECORDED,
        ALLOCATION_REVERSED,
        REFUND_APPROVED,
        DEBT_WRITTEN_OFF,
        CASH_DISCREPANCY_APPROVED,
        STOCK_ADJUSTED,
        STOCK_WRITTEN_OFF,
        PAYROLL_APPROVED,
        PAYROLL_POSTED,
        PAYROLL_RULE_APPROVED,
        PAYROLL_ADJUSTED,
        EMPLOYMENT_ENDED,
        PURCHASE_ORDER_APPROVED,
        SUPPLIER_INVOICE_REGISTERED,
        SUPPLIER_INVOICE_VOIDED,
        SUPPLIER_PAYMENT_RECORDED,
        SUPPLIER_ALLOCATION_REVERSED,
        ROLE_GRANTED,
        ROLE_REVOKED,
        CLINICAL_RECORD_READ,
        PATIENT_MERGED,
        TREATMENT_COMPLETED,
    }
)
