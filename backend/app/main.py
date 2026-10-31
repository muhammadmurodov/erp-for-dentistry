"""FastAPI application assembly.

Order matters in a couple of places and is worth stating:

* Logging is configured before anything else, so startup problems are
  structured too.
* `SecurityHeadersMiddleware` is added last and therefore runs outermost,
  which lets it stamp headers onto error responses as well.
* Domain exceptions are mapped centrally; a router never builds an HTTP error
  by hand, so the error shape stays uniform for the frontend.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.middleware import RequestContextMiddleware, SecurityHeadersMiddleware
from app.api.routers import (
    accounting,
    audit,
    auth,
    banking,
    cash,
    health,
    organizations,
    platform,
    sales,
    users,
)
from app.core.config import get_settings
from app.core.db import dispose_engine
from app.core.errors import DomainError
from app.core.logging import configure_logging, get_logger
from app.core.rate_limit import RedisRateLimiter, set_limiter

log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format)
    log.info(
        "app.starting",
        environment=settings.app_env,
        version=app.version,
    )

    # Prefer the shared Redis counter when it is reachable; a single process
    # falls back to in-memory counting and the log says which one is active.
    try:
        import redis.asyncio as aioredis

        client = aioredis.from_url(settings.redis_url, decode_responses=True)
        await client.ping()
        set_limiter(RedisRateLimiter(client))
        log.info("rate_limit.backend", backend="redis")
    except Exception as exc:
        log.warning(
            "rate_limit.backend",
            backend="in-memory",
            detail="Redis unavailable; limits apply per process only",
            error=str(exc),
        )

    try:
        yield
    finally:
        log.info("app.stopping")
        await dispose_engine()


def _serialisable_errors(exc: RequestValidationError) -> list[dict[str, Any]]:
    """Reduce Pydantic's error list to something JSON can carry.

    A validator that raises `ValueError` leaves the exception *object* in the
    error's `ctx`, and `input` can be any value the client sent. Passing
    either to `JSONResponse` raises `TypeError` and turns a 422 into a 500 —
    so every non-primitive is rendered as text here.
    """
    cleaned: list[dict[str, Any]] = []
    for error in exc.errors():
        entry: dict[str, Any] = {
            "loc": [str(part) for part in error.get("loc", ())],
            "msg": str(error.get("msg", "")),
            "type": str(error.get("type", "")),
        }
        context = error.get("ctx")
        if context:
            entry["ctx"] = {
                key: value if isinstance(value, (str, int, float, bool, type(None))) else str(value)
                for key, value in context.items()
            }
        cleaned.append(entry)
    return cleaned


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format)

    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        description=(
            "Modular ERP for small businesses in Uzbekistan. "
            "Double-entry accounting engine with organization-scoped access control."
        ),
        lifespan=lifespan,
        # No interactive docs in production: the schema describes every
        # financial endpoint and there is no reason to publish it.
        docs_url=None if settings.is_production else "/docs",
        redoc_url=None,
        openapi_url=None if settings.is_production else "/openapi.json",
    )

    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
        expose_headers=["X-Request-ID"],
        max_age=600,
    )
    app.add_middleware(SecurityHeadersMiddleware)

    @app.exception_handler(DomainError)
    async def handle_domain_error(request: Request, exc: DomainError) -> JSONResponse:
        log.info(
            "request.domain_error",
            code=exc.code,
            status_code=exc.status_code,
            path=request.url.path,
        )
        headers = {}
        if exc.status_code == 401:
            headers["WWW-Authenticate"] = "Bearer"
        if exc.status_code == 429:
            headers["Retry-After"] = str(exc.details.get("retry_after_seconds", 60))
        return JSONResponse(status_code=exc.status_code, content=exc.to_payload(), headers=headers)

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "code": "validation_failed",
                "message": "The request body is not valid",
                "details": {"errors": _serialisable_errors(exc)},
            },
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        # Never leak an internal message or stack trace to a client. The
        # request id is the handle an operator uses to find the real cause.
        log.exception("request.unhandled_error", path=request.url.path)
        return JSONResponse(
            status_code=500,
            content={
                "code": "internal_error",
                "message": "Something went wrong. Quote the request id when reporting it.",
                "details": {"request_id": getattr(request.state, "request_id", None)},
            },
        )

    app.include_router(health.router)
    for module_router in (
        auth.router,
        accounting.router,
        sales.router,
        cash.router,
        banking.router,
        organizations.router,
        users.router,
        platform.router,
        audit.router,
    ):
        app.include_router(module_router, prefix=settings.api_prefix)

    return app


app = create_app()
