"""Liveness and readiness probes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Response, status
from sqlalchemy import text

from app.api.deps import DbSession
from app.core.config import get_settings
from app.core.logging import get_logger

router = APIRouter(tags=["health"])
log = get_logger(__name__)


@router.get("/health/live")
async def live() -> dict[str, str]:
    """The process is up. Does not touch any dependency."""
    return {"status": "ok"}


@router.get("/health/ready")
async def ready(db: DbSession, response: Response) -> dict[str, Any]:
    """The process can serve traffic: the database answers.

    Returns 503 when a dependency is down, so an orchestrator takes the
    instance out of rotation instead of sending it requests that will fail.
    """
    checks: dict[str, str] = {}
    healthy = True

    try:
        await db.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as exc:  # pragma: no cover - exercised by outage only
        log.error("health.database_unavailable", error=str(exc))
        checks["database"] = "unavailable"
        healthy = False

    if not healthy:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    settings = get_settings()
    return {
        "status": "ok" if healthy else "degraded",
        "environment": settings.app_env,
        "checks": checks,
    }
