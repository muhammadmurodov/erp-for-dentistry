"""Fixed-window rate limiting.

Redis backs this in any deployment with more than one worker, because a
per-process counter is not a limit. The in-memory implementation exists for
tests and single-process development, and says so rather than pretending to be
distributed.
"""

from __future__ import annotations

import time
from collections import defaultdict
from typing import Protocol

from app.core.errors import RateLimited
from app.core.logging import get_logger

log = get_logger(__name__)


class RateLimiter(Protocol):
    async def check(self, key: str, limit: int, window_seconds: int = 60) -> None:
        """Raise `RateLimited` when `key` has exceeded `limit` in the window."""
        ...


class InMemoryRateLimiter:
    """Per-process counters. Correct only with a single worker."""

    def __init__(self) -> None:
        self._hits: dict[tuple[str, int], int] = defaultdict(int)

    async def check(self, key: str, limit: int, window_seconds: int = 60) -> None:
        window = int(time.time() // window_seconds)
        bucket = (key, window)
        self._hits[bucket] += 1
        if self._hits[bucket] > limit:
            raise RateLimited(
                "Too many requests. Please wait a moment and try again.",
                details={"retry_after_seconds": window_seconds},
            )
        # Keep the dictionary from growing without bound.
        if len(self._hits) > 10_000:
            for stale_key in [k for k in self._hits if k[1] < window]:
                del self._hits[stale_key]

    def reset(self) -> None:
        self._hits.clear()


class RedisRateLimiter:
    def __init__(self, client: object) -> None:
        self._client = client

    async def check(self, key: str, limit: int, window_seconds: int = 60) -> None:
        window = int(time.time() // window_seconds)
        redis_key = f"ratelimit:{key}:{window}"
        try:
            pipe = self._client.pipeline()  # type: ignore[attr-defined]
            pipe.incr(redis_key)
            pipe.expire(redis_key, window_seconds + 1)
            count, _ = await pipe.execute()
        except Exception:
            # A rate limiter outage must not take the API down with it. Log
            # loudly: requests are passing unthrottled until Redis returns.
            log.warning("rate_limit.backend_unavailable", key=key, exc_info=True)
            return
        if int(count) > limit:
            raise RateLimited(
                "Too many requests. Please wait a moment and try again.",
                details={"retry_after_seconds": window_seconds},
            )


_limiter: RateLimiter = InMemoryRateLimiter()


def set_limiter(limiter: RateLimiter) -> None:
    global _limiter
    _limiter = limiter


def get_limiter() -> RateLimiter:
    return _limiter
