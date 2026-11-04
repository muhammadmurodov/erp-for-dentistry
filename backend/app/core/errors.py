"""Domain error hierarchy and its mapping onto HTTP responses.

Business modules raise these; the API layer never needs to know how a rule was
violated in order to answer correctly. Every error carries a stable machine
`code` so the frontend can localise the message itself.
"""

from __future__ import annotations

from typing import Any


class DomainError(Exception):
    """Base class for every expected business-rule failure."""

    status_code: int = 400
    code: str = "domain_error"

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.details:
            payload["details"] = self.details
        return payload


class ValidationFailed(DomainError):
    status_code = 422
    code = "validation_failed"


class NotFound(DomainError):
    status_code = 404
    code = "not_found"


class Conflict(DomainError):
    """The request contradicts the current state of the record."""

    status_code = 409
    code = "conflict"


class AuthenticationFailed(DomainError):
    status_code = 401
    code = "authentication_failed"


class PermissionDenied(DomainError):
    status_code = 403
    code = "permission_denied"


class CrossOrganizationAccess(PermissionDenied):
    """A record was addressed from outside the organization that owns it.

    Deliberately reported as a plain 403 with no detail about whether the
    record exists, so identifiers cannot be probed across tenants.
    """

    code = "permission_denied"


class RateLimited(DomainError):
    status_code = 429
    code = "rate_limited"


class ModuleNotAvailable(DomainError):
    """A caller reached an endpoint whose module is not implemented yet."""

    status_code = 501
    code = "module_not_available"


# --- accounting-specific -----------------------------------------------------


class AccountingError(DomainError):
    status_code = 422
    code = "accounting_error"


class UnbalancedEntry(AccountingError):
    code = "unbalanced_entry"


class PeriodClosed(AccountingError):
    status_code = 409
    code = "period_closed"


class EntryImmutable(AccountingError):
    status_code = 409
    code = "entry_immutable"


class DuplicatePostingEvent(AccountingError):
    status_code = 409
    code = "duplicate_posting_event"


class PolicyRuleMissing(AccountingError):
    """The active policy has no rule for the operation being attempted.

    Deliberately **not** a `NotFound`. Nothing is missing that the caller
    asked for by id; what is missing is a decision an accountant has not
    made yet, and the only useful response names the operation so somebody
    can add the rule. A 404 would be read as "that invoice does not exist",
    which is both wrong and unactionable — and is exactly how a browser pass
    found this: a supplier invoice refusing to register with "the requested
    record was not found".

    409 rather than 422: the request is well formed and would succeed once
    the policy says how to post it.
    """

    status_code = 409
    code = "policy_rule_missing"
