"""Superadmin system health (SUP-4): each probe isolated, failures reported
as data. httpx transport injectable for tests. Org rollups delegate to
modules/quotas (Plan F's platform_usage_by_org) - one aggregation code path.
"""

from typing import Any

import httpx
from redis.asyncio import Redis

from raghub.core.config import Settings

CELERY_QUEUES = ("default", "interactive")


async def queue_depths(redis: Redis) -> dict[str, Any]:
    try:
        depths: dict[str, int] = {}
        for q in CELERY_QUEUES:
            # redis-py's llen() is stubbed Union[Awaitable[int], int] (a known
            # sync/async typing gap in that library, not a real ambiguity here).
            depths[q] = int(await redis.llen(q))  # type: ignore[misc]
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
