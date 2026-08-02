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


@router.get("/health")
async def system_health(
    request: Request, session: SessionDep, settings: SettingsDep, ctx: SuperadminDep
) -> dict[str, Any]:
    transport = request.app.state.litellm_transport  # tests inject; prod None
    # HTTP/redis probes gather concurrently; the DB rollup runs on the request
    # session afterward (one AsyncSession must never be shared across tasks).
    queues, qdrant, litellm = await asyncio.gather(
        ops_health.queue_depths(request.app.state.redis),
        ops_health.qdrant_stats(settings, transport),
        ops_health.litellm_health(settings, transport),
    )
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
        "queues": queues,
        "qdrant": qdrant,
        "litellm": litellm,
        "orgs": orgs,
    }
