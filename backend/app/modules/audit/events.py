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
PAYMENT_RECORDED: Final = "sales.payment.recorded"
REFUND_REQUESTED: Final = "sales.refund.requested"
REFUND_APPROVED: Final = "sales.refund.approved"
CASH_SESSION_OPENED: Final = "cash.session.opened"
CASH_SESSION_CLOSED: Final = "cash.session.closed"
CASH_COUNT_SUBMITTED: Final = "cash.count.submitted"
CASH_DISCREPANCY_APPROVED: Final = "cash.discrepancy.approved"
STATEMENT_IMPORTED: Final = "banking.statement.imported"
RECONCILIATION_MATCHED: Final = "banking.reconciliation.matched"

# --- inventory and payroll (Phase 4+) --------------------------------------
STOCK_RECEIVED: Final = "inventory.stock.received"
STOCK_ADJUSTED: Final = "inventory.stock.adjusted"
PAYROLL_CALCULATED: Final = "payroll.run.calculated"
PAYROLL_APPROVED: Final = "payroll.run.approved"
PAYROLL_POSTED: Final = "payroll.run.posted"

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
        PAYMENT_RECORDED,
        REFUND_APPROVED,
        CASH_DISCREPANCY_APPROVED,
        STOCK_ADJUSTED,
        PAYROLL_APPROVED,
        PAYROLL_POSTED,
        ROLE_GRANTED,
        ROLE_REVOKED,
        CLINICAL_RECORD_READ,
    }
)
