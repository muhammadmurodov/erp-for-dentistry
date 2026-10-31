"""Database engine, session factory and the declarative base.

One async engine per process. Sessions are request-scoped and are never
committed by the API layer: an application service owns its transaction so
that a failed posting rolls back whole rather than in part.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, MetaData, func, text
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.pool import NullPool

from app.core.config import get_settings

# Explicit naming so Alembic autogenerate produces stable, reviewable names
# for constraints and indexes instead of database-assigned ones.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        pk = getattr(self, "id", None)
        return f"<{type(self).__name__} id={pk}>"


class UUIDPrimaryKeyMixin:
    """UUID surrogate key, generated in the database.

    Sequential integers would let one organization probe another's record
    count and guess identifiers; `gen_random_uuid()` does not.
    """

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )


class TimestampMixin:
    """Creation and modification instants, always stored in UTC."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        settings = get_settings()
        kwargs: dict[str, Any] = {
            "echo": False,
            # Statement cache off: pgbouncer in transaction mode (the likely
            # production deployment) cannot keep prepared statements.
            "connect_args": {"statement_cache_size": 0},
        }
        if settings.app_env == "test":
            # Each test gets its own event loop, and a pooled connection
            # cannot move between loops — it surfaces as "attached to a
            # different loop" from somewhere unrelated. NullPool opens and
            # closes per use, which keeps tests independent.
            kwargs["poolclass"] = NullPool
        else:
            kwargs.update(pool_pre_ping=True, pool_size=10, max_overflow=20)
        _engine = create_async_engine(settings.database_url, **kwargs)
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            bind=get_engine(),
            expire_on_commit=False,
            autoflush=False,
        )
    return _session_factory


async def get_db_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding a session that rolls back on error.

    The dependency never commits. A service that changed data commits its own
    unit of work, which keeps transaction boundaries where the business rules
    are rather than at the edge of the HTTP layer.
    """
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


async def dispose_engine() -> None:
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _session_factory = None


def reset_engine_for_tests() -> None:  # pragma: no cover
    """Forget the cached engine and session factory.

    Called between tests so that the next one builds its own engine on its own
    event loop. Does not await `dispose()`: the previous loop is already
    closed, so there is nothing left to await on.
    """
    global _engine, _session_factory
    _engine = None
    _session_factory = None
