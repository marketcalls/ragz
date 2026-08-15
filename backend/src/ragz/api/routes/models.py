from typing import Annotated
from uuid import UUID

import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ragz.api.deps import get_session
from ragz.core.config import Settings, get_settings
from ragz.core.errors import UpstreamError
from ragz.modules.models import service
from ragz.modules.models.catalog import ModelCatalogEntry, refresh_catalog
from ragz.modules.models.models import Model
from ragz.modules.models.schemas import ModelCreate, ModelOut, ModelPatch, ModelPublic
from ragz.modules.models.sync import sync_models_to_litellm
from ragz.modules.tenancy.context import (
    TenantContext,
    get_tenant_context,
    require_action,
    require_role,
)

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
    except Exception:
        # Never let an unexpected error escape a background task: FastAPI has
        # already sent the response, so an unhandled raise here can't surface
        # to the client - it would just leave rows stuck 'pending' forever
        # while the admin UI polls for a terminal state. structlog is the
        # only place this is observable.
        structlog.get_logger().warning("model_sync_background_failed", exc_info=True)


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
        mock_response=body.mock_response, tools_unreliable=body.tools_unreliable,
        supports_reasoning=body.supports_reasoning,
        default_reasoning_effort=body.default_reasoning_effort,
        supports_vision=body.supports_vision,
        modality=body.modality, dimension=body.dimension,
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
        mock_response=body.mock_response, tools_unreliable=body.tools_unreliable,
        supports_reasoning=body.supports_reasoning,
        default_reasoning_effort=body.default_reasoning_effort,
        supports_vision=body.supports_vision,
        is_utility=body.is_utility,
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


class CatalogEntryOut(BaseModel):
    name: str
    provider: str
    mode: str | None  # LiteLLM mode: chat | embedding | image_generation | ... —
    # drives the add-model picker's Type filter (DOC-10): an "Embedding model"
    # selection lists only mode=="embedding" entries, so text-embedding-* aren't
    # buried under newer chat/image/audio models sorted ahead of them.
    max_input_tokens: int | None
    input_cost_per_1m: float | None  # derived: input_cost_per_token * 1e6
    output_cost_per_1m: float | None
    position: int  # source-JSON enumeration order; higher ~= newer release
    registered: bool

    model_config = {"from_attributes": False}


class CatalogOut(BaseModel):
    entries: list[CatalogEntryOut]
    new_available: int


@router.get("/admin/models/catalog", response_model=CatalogOut)
async def get_catalog(session: SessionDep, ctx: SuperadminDep) -> CatalogOut:
    """MODEL-10/G7: LiteLLM's pricing/context-window catalog, cross-referenced
    against the registry so the admin UI can flag models not yet added.

    Ordered (provider ASC, position DESC): the add-model picker groups by
    provider and shows the newest models first within each provider."""
    entries = (
        await session.execute(
            select(ModelCatalogEntry).order_by(
                ModelCatalogEntry.provider, ModelCatalogEntry.position.desc()
            )
        )
    ).scalars().all()
    registered = set((await session.execute(select(Model.litellm_model_name))).scalars())
    out = [
        CatalogEntryOut(
            name=e.name, provider=e.provider, mode=e.mode,
            max_input_tokens=e.max_input_tokens,
            input_cost_per_1m=(
                e.input_cost_per_token * 1e6 if e.input_cost_per_token is not None else None
            ),
            output_cost_per_1m=(
                e.output_cost_per_token * 1e6 if e.output_cost_per_token is not None else None
            ),
            position=e.position,
            registered=e.name in registered,
        )
        for e in entries
    ]
    return CatalogOut(entries=out, new_available=sum(1 for e in out if not e.registered))


@router.post("/admin/models/catalog/refresh")
async def force_refresh_catalog(
    session: SessionDep, settings: SettingsDep, ctx: SuperadminDep
) -> dict[str, int]:
    return {"upserted": await refresh_catalog(session, settings, force=True)}


@router.get("/models", response_model=list[ModelPublic])
async def list_public_models(
    session: SessionDep,
    # sec RAGZ-PUB-01b: enforce the declared models.read action (was bare auth,
    # so a custom role denying models.read could still list them).
    ctx: Annotated[TenantContext, Depends(require_action("models.read"))],
) -> list[ModelPublic]:
    return [
        ModelPublic.model_validate(m)
        for m in await service.list_enabled_models(session, modality="chat")
    ]
