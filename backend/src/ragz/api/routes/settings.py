from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ragz.api.deps import get_session
from ragz.core.config import Settings, get_settings
from ragz.modules.models import settings_service
from ragz.modules.models.schemas import ProviderSettingsOut, ProviderSettingsUpdate
from ragz.modules.tenancy.context import TenantContext, require_role

router = APIRouter(tags=["settings"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
# require_role() with no roles -> superadmin-only (only the bypass passes).
SuperadminDep = Annotated[TenantContext, Depends(require_role())]


@router.get("/admin/settings", response_model=ProviderSettingsOut)
async def get_settings_route(session: SessionDep, ctx: SuperadminDep) -> ProviderSettingsOut:
    return await settings_service.get_provider_settings(session)


@router.put("/admin/settings", response_model=ProviderSettingsOut)
async def put_settings_route(
    body: ProviderSettingsUpdate, session: SessionDep,
    settings: SettingsDep, ctx: SuperadminDep,
) -> ProviderSettingsOut:
    return await settings_service.update_provider_settings(
        session, settings, actor_id=ctx.user_id, patch=body
    )
