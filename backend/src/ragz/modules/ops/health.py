"""Superadmin system health (SUP-4): each probe isolated, failures reported
as data. httpx transport injectable for tests. Org rollups delegate to
modules/quotas (Plan F's platform_usage_by_org) - one aggregation code path.
"""

import asyncio
from typing import Any

import httpx
from redis.asyncio import Redis

from ragz.core.config import Settings

CELERY_QUEUES = ("default", "interactive")
# Module-level so tests can monkeypatch it to a small value and exercise the
# timeout path quickly, without a blackholed Redis actually hanging for 5s.
QUEUE_TIMEOUT_SECONDS = 5.0


async def queue_depths(redis: Redis, timeout: float | None = None) -> dict[str, Any]:
    """LLEN each Celery queue, bounded by `timeout` (default QUEUE_TIMEOUT_SECONDS)
    so a blackholed Redis degrades the component instead of hanging the gather
    forever."""
    effective_timeout = QUEUE_TIMEOUT_SECONDS if timeout is None else timeout

    async def _llen(key: str) -> int:
        # redis-py's llen() is stubbed Union[Awaitable[int], int] (a known
        # sync/async typing gap in that library, not a real ambiguity here).
        # Wrapping it in a coroutine normalizes it to a plain Awaitable[int]
        # so asyncio.wait_for's overloads are satisfied.
        return int(await redis.llen(key))  # type: ignore[misc]

    try:
        depths: dict[str, int] = {}
        for q in CELERY_QUEUES:
            depths[q] = await asyncio.wait_for(_llen(q), timeout=effective_timeout)
        return {"status": "ok", "depths": depths}
    except Exception as exc:  # noqa: BLE001 - health must degrade, not raise
        return {"status": "error", "detail": type(exc).__name__}


async def qdrant_stats(
    settings: Settings, transport: httpx.AsyncBaseTransport | None = None
) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(
            base_url=settings.qdrant_url, transport=transport, timeout=5.0
        ) as client:
            names = [
                c["name"]
                for c in (await client.get("/collections"))
                .raise_for_status()
                .json()["result"]["collections"]
            ]
            collections = []
            for name in names:
                info = (await client.get(f"/collections/{name}")).raise_for_status().json()
                collections.append(
                    {"name": name, "points_count": info["result"].get("points_count", 0)}
                )
        return {"status": "ok", "collections": collections}
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "detail": type(exc).__name__}


async def litellm_health(
    settings: Settings, transport: httpx.AsyncBaseTransport | None = None
) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(
            base_url=settings.litellm_url, transport=transport, timeout=5.0
        ) as client:
            (await client.get("/health/liveliness")).raise_for_status()
        return {"status": "ok"}
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "detail": type(exc).__name__}
