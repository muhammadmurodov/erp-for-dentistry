"""HTTP middleware: correlation ids, security headers, access logging."""

from __future__ import annotations

import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.core.logging import (
    actor_id_var,
    get_logger,
    organization_id_var,
    request_id_var,
)

log = get_logger("api.access")

#: Applied to every response. The API serves JSON only, so a restrictive CSP
#: costs nothing here; the frontend sets its own.
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
    "Cache-Control": "no-store",
}


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Assigns a request id and clears the logging context afterwards."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        incoming = request.headers.get("x-request-id", "")
        # Never echo an unvalidated header into logs: accept it only when it
        # already looks like a request id.
        request_id = incoming if _looks_like_id(incoming) else uuid.uuid4().hex
        token = request_id_var.set(request_id)
        request.state.request_id = request_id
        started = time.perf_counter()
        try:
            response = await call_next(request)
        finally:
            duration_ms = round((time.perf_counter() - started) * 1000, 2)
            request_id_var.reset(token)
            actor_id_var.set(None)
            organization_id_var.set(None)
        response.headers["X-Request-ID"] = request_id
        log.info(
            "request.completed",
            method=request.method,
            path=request.url.path,
            status_code=response.status_code,
            duration_ms=duration_ms,
            request_id=request_id,
        )
        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        for header, value in SECURITY_HEADERS.items():
            response.headers.setdefault(header, value)
        if request.url.scheme == "https":
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )
        return response


def _looks_like_id(value: str) -> bool:
    return 8 <= len(value) <= 64 and all(c.isalnum() or c in "-_" for c in value)
