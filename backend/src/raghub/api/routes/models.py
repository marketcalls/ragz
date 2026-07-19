from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from raghub.api.deps import get_session
from raghub.core.config import Settings, get_settings
from raghub.core.errors import UpstreamError
from raghub.modules.models import service
from raghub.modules.models.schemas import ModelCreate, ModelOut, ModelPatch, ModelPublic
from raghub.modules.models.sync import sync_models_to_litellm
from raghub.modules.tenancy.context import TenantContext, get_tenant_context, require_role

router = APIRouter(tags=["models"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
CtxDep = Annotated[TenantContext, Depends(get_tenant_context)]
# require_role() with no roles -> superadmin-only (only the bypass passes).
SuperadminDep = Annotated[TenantContext, Depends(require_role())]


async def _background_sync(app_state: object, settings: Settings) -> None:
    """Replay on a FRESH session - the request session is closed by the time a
    background task runs (it fires after the response is sent)."""
    factory = app_state.session_factory  # type: ignore[attr-defined]
    try:
        async with factory() as session:
            await sync_models_to_litellm(
                session, settings, transport=app_state.litellm_transport  # type: ignore[attr-defined]
            )
    except UpstreamError:
        pass  # sync_status='error' already persisted by sync_models_to_litellm


def _schedule_sync(background_tasks: BackgroundTasks, request: Request, settings: Settings) -> None:
    background_tasks.add_task(_background_sync, request.app.state, settings)


@router.get("/admin/models", response_model=list[ModelOut])
async def list_models(session: SessionDep, ctx: SuperadminDep) -> list[ModelOut]:
    return await service.to_model_out(session, await service.list_models(session))


@router.post("/admin/models", status_code=201, response_model=ModelOut)
async def create_model(
    body: ModelCreate, request: Request, session: SessionDep,
    settings: SettingsDep, ctx: SuperadminDep, background_tasks: BackgroundTasks,
) -> ModelOut:
    """Creates the row and returns immediately; the LiteLLM replay runs as a
    background task on a fresh session (each replay is N+1 HTTP calls to the
    proxy, so it must not block the request). The response therefore reflects
    the row's pre-sync sync_status - the admin models page polls/reloads to see
    the post-replay outcome (synced|error), which is the observable contract."""
    model = await service.create_model(
        session, ctx, litellm_model_name=body.litellm_model_name,
        display_name=body.display_name, provider_kind=body.provider_kind,
        base_url=body.base_url, api_key=body.api_key, settings=settings,
    )
    _schedule_sync(background_tasks, request, settings)
    return (await service.to_model_out(session, [model]))[0]


@router.patch("/admin/models/{model_id}", response_model=ModelOut)
async def patch_model(
    model_id: UUID, body: ModelPatch, request: Request, session: SessionDep,
    settings: SettingsDep, ctx: SuperadminDep, background_tasks: BackgroundTasks,
) -> ModelOut:
    """See create_model docstring: sync now runs in the background."""
    model = await service.update_model(
        session, ctx, model_id, display_name=body.display_name, base_url=body.base_url,
        enabled=body.enabled, api_key=body.api_key, settings=settings,
    )
    _schedule_sync(background_tasks, request, settings)
    return (await service.to_model_out(session, [model]))[0]


@router.delete("/admin/models/{model_id}", status_code=204)
async def delete_model(
    model_id: UUID, request: Request, session: SessionDep,
    settings: SettingsDep, ctx: SuperadminDep, background_tasks: BackgroundTasks,
) -> None:
    """See create_model docstring: sync now runs in the background."""
    await service.delete_model(session, ctx, model_id, settings=settings)
    _schedule_sync(background_tasks, request, settings)


@router.get("/models", response_model=list[ModelPublic])
async def list_public_models(session: SessionDep, ctx: CtxDep) -> list[ModelPublic]:
    return [ModelPublic.model_validate(m) for m in await service.list_enabled_models(session)]
