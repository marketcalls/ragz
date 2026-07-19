import asyncio
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from raghub.api.deps import get_session
from raghub.core.config import Settings, get_settings
from raghub.modules.ops import health as ops_health
from raghub.modules.quotas.service import platform_usage_by_org  # Plan F (contract C2)
from raghub.modules.tenancy.context import TenantContext, require_role

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
    orgs = await platform_usage_by_org(session, days=30)
    return {
        "queues": queues,
        "qdrant": qdrant,
        "litellm": litellm,
        "orgs": orgs,  # F's platform_usage_by_org already returns plain rows
    }
