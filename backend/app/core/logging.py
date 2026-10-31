"""Structured logging and per-request correlation ids.

Every log line carries the request id, and — once authentication has run —
the acting user and organization. Sensitive fields are dropped by
`_redact_sensitive` before anything reaches a handler, so a careless
`log.info("...", password=...)` cannot leak a credential.
"""

from __future__ import annotations

import logging
import sys
from contextvars import ContextVar
from typing import Any

import structlog

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
actor_id_var: ContextVar[str | None] = ContextVar("actor_id", default=None)
organization_id_var: ContextVar[str | None] = ContextVar("organization_id", default=None)

# Keys that must never be written to a log, whatever the call site passes.
SENSITIVE_KEYS = frozenset(
    {
        "password",
        "new_password",
        "current_password",
        "password_hash",
        "token",
        "access_token",
        "refresh_token",
        "token_hash",
        "secret",
        "secret_key",
        "authorization",
        "api_key",
        "card_number",
        "pan",
        "cvv",
        "diagnosis",
        "anamnesis",
        "clinical_note",
    }
)
REDACTED = "[redacted]"


def _redact_sensitive(_logger: Any, _method: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    for key in list(event_dict):
        if key.lower() in SENSITIVE_KEYS:
            event_dict[key] = REDACTED
    return event_dict


def _bind_context(_logger: Any, _method: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    for key, var in (
        ("request_id", request_id_var),
        ("actor_id", actor_id_var),
        ("organization_id", organization_id_var),
    ):
        value = var.get()
        if value is not None:
            event_dict.setdefault(key, value)
    return event_dict


def configure_logging(level: str = "INFO", fmt: str = "json") -> None:
    renderer: Any = (
        structlog.processors.JSONRenderer()
        if fmt == "json"
        else structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.stdlib.add_logger_name,
            _bind_context,
            _redact_sensitive,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelNamesMapping().get(level.upper(), logging.INFO)
        ),
        # The stdlib factory (rather than PrintLogger) is required by the
        # `add_logger_name` processor and lets library logs share the format.
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level.upper(), force=True)
    for noisy in ("uvicorn.access", "sqlalchemy.engine.Engine"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)  # type: ignore[return-value]
