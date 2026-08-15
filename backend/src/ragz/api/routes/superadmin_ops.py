import asyncio
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ragz.api.deps import get_session
from ragz.core.config import Settings, get_settings
from ragz.modules.ops import health as ops_health
from ragz.modules.quotas.service import platform_usage_by_org  # Plan F (contract C2)
from ragz.modules.tenancy.context import TenantContext, require_role

router = APIRouter(prefix="/superadmin", tags=["superadmin"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
SuperadminDep = Annotated[TenantContext, Depends(require_role())]


async def _disabled_embedder() -> dict[str, Any]:
    """Health-row shape for a local TEI embedder that is intentionally off
    (hosted embedder configured). Not an error -- rendered as a neutral
    'Disabled' badge, no latency."""
    return {
        "status": "disabled",
        "detail": "local embedder disabled; embeddings served by hosted provider",
    }


@router.get("/health")
async def system_health(
    request: Request, session: SessionDep, settings: SettingsDep, ctx: SuperadminDep
) -> dict[str, Any]:
    transport = request.app.state.litellm_transport  # tests inject; prod None
    # Whether to probe the local TEI embedder at all. When the platform is
    # switched to a hosted embedder, the local TEI model is disabled in the
    # registry and its container is intentionally stopped -- probing it would
    # report a misleading red "error" though embeddings work fine via the
    # hosted provider (covered by the litellm probe). Run this session query
    # BEFORE the concurrent gather (the gather's tasks touch redis/HTTP, never
    # the session) so one AsyncSession is never used from concurrent tasks.
    local_embedder_enabled = await ops_health.local_embedder_in_use(session)
    embedder_probe = (
        ops_health.embedder_health(settings, transport)
        if local_embedder_enabled
        else _disabled_embedder()
    )
    # HTTP/redis probes gather concurrently -- each opens its own client (or,
    # for redis, uses the connection-pooled shared client), so concurrent use
    # is safe. The DB probe and the org rollup run on the request session
    # AFTERWARD, sequentially: one AsyncSession must never be shared across
    # concurrent tasks, and that includes db_health itself.
    queues, qdrant, litellm, redis_status, embedder, reranker, minio = await asyncio.gather(
        ops_health.queue_depths(request.app.state.redis),
        ops_health.qdrant_stats(settings, transport),
        ops_health.litellm_health(settings, transport),
        ops_health.redis_health(request.app.state.redis),
        embedder_probe,
        ops_health.reranker_health(settings, transport),
        ops_health.minio_health(settings),
    )
    db = await ops_health.db_health(session)
    # Unlike the other probes, the brief's example payload keeps "orgs" as a
    # bare list on success (no status wrapper) - so only the error path gets
    # the {"status": "error", "detail": ...} shape; success stays a plain
    # list to match the documented contract exactly.
    orgs: Any
    try:
        orgs = await platform_usage_by_org(session, days=30)
    except Exception as exc:  # noqa: BLE001 - health must degrade, not raise
        orgs = {"status": "error", "detail": type(exc).__name__}
    return {
        "db": db,
        "redis": redis_status,
        "queues": queues,
        "qdrant": qdrant,
        "minio": minio,
        "embedder": embedder,
        "reranker": reranker,
        "litellm": litellm,
        "orgs": orgs,
    }
