"""Superadmin system health (SUP-4): each probe isolated, failures reported
as data. httpx transport injectable for tests. Org rollups delegate to
modules/quotas (Plan F's platform_usage_by_org) - one aggregation code path.
"""

import asyncio
import time
from typing import Any

import httpx
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ragz.core.config import Settings
from ragz.core.storage import build_storage

CELERY_QUEUES = ("default", "interactive")
# Module-level so tests can monkeypatch it to a small value and exercise the
# timeout path quickly, without a blackholed Redis actually hanging for 5s.
QUEUE_TIMEOUT_SECONDS = 5.0

# Bounded timeout shared by the db/redis/minio probes below (embedder/reranker
# use httpx's own client timeout instead, see below) -- module-level so tests
# can monkeypatch it down like QUEUE_TIMEOUT_SECONDS, and so a single hung
# dependency can never stall /superadmin/health past this ceiling.
PROBE_TIMEOUT_SECONDS = 5.0


def _elapsed_ms(start: float) -> int:
    return int((time.monotonic() - start) * 1000)


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


async def db_health(session: AsyncSession, timeout: float | None = None) -> dict[str, Any]:
    """Timed `SELECT 1` on the request's own DB session. Callers must not run
    this concurrently with other work on the same `session` -- AsyncSession
    is not safe for concurrent use (mirrors the platform_usage_by_org rollup
    below it in superadmin_ops.system_health, which shares the same session)."""
    effective_timeout = PROBE_TIMEOUT_SECONDS if timeout is None else timeout
    start = time.monotonic()
    try:
        await asyncio.wait_for(session.execute(text("SELECT 1")), timeout=effective_timeout)
        return {"status": "ok", "latency_ms": _elapsed_ms(start)}
    except Exception as exc:  # noqa: BLE001 - health must degrade, not raise
        return {"status": "error", "detail": type(exc).__name__, "latency_ms": _elapsed_ms(start)}


async def redis_health(redis: Redis, timeout: float | None = None) -> dict[str, Any]:
    """Timed PING against the shared Redis client, bounded by `timeout`
    (default PROBE_TIMEOUT_SECONDS) so a blackholed Redis degrades this
    component instead of hanging the gather forever."""
    effective_timeout = PROBE_TIMEOUT_SECONDS if timeout is None else timeout

    async def _ping() -> bool:
        return bool(await redis.ping())

    start = time.monotonic()
    try:
        await asyncio.wait_for(_ping(), timeout=effective_timeout)
        return {"status": "ok", "latency_ms": _elapsed_ms(start)}
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "detail": type(exc).__name__, "latency_ms": _elapsed_ms(start)}


async def embedder_health(
    settings: Settings, transport: httpx.AsyncBaseTransport | None = None, timeout: float = 5.0
) -> dict[str, Any]:
    """Timed GET {tei_url}/health -- the TEI embedder's liveness endpoint.
    Bounded by BOTH httpx's own client timeout (covers real network hangs)
    AND an explicit asyncio.wait_for (belt-and-suspenders -- a mocked
    transport in tests doesn't honor httpx's timeout the way a real socket
    does) so a slow/hung embedder always shows up as a fast error or high
    latency, never a long hang. This was the actual incident: a 12s-slow
    embedder was invisible before this probe existed."""
    start = time.monotonic()
    try:
        async with httpx.AsyncClient(
            base_url=settings.tei_url, transport=transport, timeout=timeout
        ) as client:
            (await asyncio.wait_for(client.get("/health"), timeout=timeout)).raise_for_status()
        return {"status": "ok", "latency_ms": _elapsed_ms(start)}
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "detail": type(exc).__name__, "latency_ms": _elapsed_ms(start)}


async def reranker_health(
    settings: Settings, transport: httpx.AsyncBaseTransport | None = None, timeout: float = 5.0
) -> dict[str, Any]:
    """Timed GET {rerank_url}/health -- the TEI reranker's liveness endpoint.
    Same double-bound (httpx timeout + explicit wait_for) as embedder_health
    above, so a down reranker (connection refused) or a hung one both
    degrade fast rather than blocking the response."""
    start = time.monotonic()
    try:
        async with httpx.AsyncClient(
            base_url=settings.rerank_url, transport=transport, timeout=timeout
        ) as client:
            (await asyncio.wait_for(client.get("/health"), timeout=timeout)).raise_for_status()
        return {"status": "ok", "latency_ms": _elapsed_ms(start)}
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "detail": type(exc).__name__, "latency_ms": _elapsed_ms(start)}


async def minio_health(settings: Settings, timeout: float | None = None) -> dict[str, Any]:
    """Timed bucket HEAD against MinIO/S3 -- observes only, never creates the
    bucket (see ObjectStorage.head_bucket)."""
    effective_timeout = PROBE_TIMEOUT_SECONDS if timeout is None else timeout
    start = time.monotonic()
    try:
        storage = build_storage(settings)
        await asyncio.wait_for(storage.head_bucket(), timeout=effective_timeout)
        return {"status": "ok", "latency_ms": _elapsed_ms(start)}
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "detail": type(exc).__name__, "latency_ms": _elapsed_ms(start)}
