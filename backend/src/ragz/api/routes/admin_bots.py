from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ragz.api.deps import get_session
from ragz.core.config import Settings, get_settings
from ragz.modules.audit.service import record_audit
from ragz.modules.bots import service as svc
from ragz.modules.bots.models import BotIntegration
from ragz.modules.bots.schemas import BotIntegrationCreate, BotIntegrationOut, BotIntegrationPatch
from ragz.modules.tenancy.context import TenantContext, require_role

router = APIRouter(tags=["admin-bots"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
# require_role() with no roles -> superadmin-only (only the bypass passes).
SuperadminDep = Annotated[TenantContext, Depends(require_role())]


def _to_out(row: BotIntegration, settings: Settings) -> BotIntegrationOut:
    return BotIntegrationOut(
        id=row.id, platform=row.platform, name=row.name, org_id=row.org_id,  # type: ignore[arg-type]
        workspace_id=row.workspace_id, user_id=row.user_id, webhook_id=row.webhook_id,
        webhook_url=f"{settings.public_api_base_url}/external/bots/{row.platform}/{row.webhook_id}",
        enabled=row.enabled, created_by=row.created_by, created_at=row.created_at,
    )


@router.post("/admin/bots", status_code=201, response_model=BotIntegrationOut)
async def create_bot(
    body: BotIntegrationCreate, session: SessionDep, settings: SettingsDep, ctx: SuperadminDep,
) -> BotIntegrationOut:
    row = await svc.create_integration(
        session, settings, actor_id=ctx.user_id, platform=body.platform, name=body.name,
        workspace_id=body.workspace_id, user_id=body.user_id, token=body.token,
        signing_secret=body.signing_secret,
    )
    await record_audit(
        session, org_id=row.org_id, actor_id=ctx.user_id, action="bot.created",
        target_type="bot_integration", target_id=str(row.id),
    )
    await session.commit()
    return _to_out(row, settings)


@router.get("/admin/bots", response_model=list[BotIntegrationOut])
async def list_bots(
    session: SessionDep, settings: SettingsDep, ctx: SuperadminDep
) -> list[BotIntegrationOut]:
    return [_to_out(row, settings) for row in await svc.list_integrations(session)]


@router.patch("/admin/bots/{bot_id}", response_model=BotIntegrationOut)
async def patch_bot(
    bot_id: UUID, body: BotIntegrationPatch, session: SessionDep,
    settings: SettingsDep, ctx: SuperadminDep,
) -> BotIntegrationOut:
    row = await svc.set_enabled(session, integration_id=bot_id, enabled=body.enabled)
    await record_audit(
        session, org_id=row.org_id, actor_id=ctx.user_id,
        action="bot.enabled" if body.enabled else "bot.disabled",
        target_type="bot_integration", target_id=str(bot_id),
    )
    await session.commit()
    return _to_out(row, settings)


@router.delete("/admin/bots/{bot_id}", status_code=204)
async def delete_bot(
    bot_id: UUID, session: SessionDep, settings: SettingsDep, ctx: SuperadminDep
) -> None:
    row = await svc.get_integration(session, integration_id=bot_id)
    org_id = row.org_id
    await svc.delete_integration(session, settings, actor_id=ctx.user_id, integration_id=bot_id)
    await record_audit(
        session, org_id=org_id, actor_id=ctx.user_id, action="bot.deleted",
        target_type="bot_integration", target_id=str(bot_id),
    )
    await session.commit()
