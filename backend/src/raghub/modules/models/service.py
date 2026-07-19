from uuid import UUID

from sqlalchemy import select, true
from sqlalchemy.ext.asyncio import AsyncSession

from raghub.core.config import Settings
from raghub.core.errors import ConflictError, NotFoundError
from raghub.modules.audit.service import record_audit
from raghub.modules.models.models import Model
from raghub.modules.models.schemas import ModelOut
from raghub.modules.secrets import service as secrets_service
from raghub.modules.tenancy.context import TenantContext


async def get_model(session: AsyncSession, model_id: UUID) -> Model:
    model = (
        await session.execute(select(Model).where(Model.id == model_id))
    ).scalar_one_or_none()
    if model is None:
        raise NotFoundError("model not found")
    return model


async def list_models(session: AsyncSession) -> list[Model]:
    return list((await session.execute(select(Model).order_by(Model.created_at))).scalars())


async def list_enabled_models(session: AsyncSession) -> list[Model]:
    stmt = select(Model).where(Model.enabled == true()).order_by(Model.created_at)
    return list((await session.execute(stmt)).scalars())


async def _enabled_model(session: AsyncSession, model_id: UUID) -> Model | None:
    return (
        await session.execute(
            select(Model).where(Model.id == model_id, Model.enabled == true())
        )
    ).scalar_one_or_none()


async def resolve_model(
    session: AsyncSession, *, requested_model_id: UUID | None, default_model_id: UUID | None
) -> Model:
    """Chat model resolution (spec 3.5 + Plan D model selector):
    explicit request -> workspace default -> typed error."""
    if requested_model_id is not None:
        model = await _enabled_model(session, requested_model_id)
        if model is None:
            raise NotFoundError("model not found or disabled")
        return model
    if default_model_id is not None:
        model = await _enabled_model(session, default_model_id)
        if model is not None:
            return model
    raise ConflictError("no model configured for workspace")


async def to_model_out(session: AsyncSession, models: list[Model]) -> list[ModelOut]:
    """Serialize with the key fingerprint joined in from the secrets module."""
    fingerprints = {s.name: s.fingerprint for s in await secrets_service.list_secrets(session)}
    return [
        ModelOut(
            id=m.id,
            litellm_model_name=m.litellm_model_name,
            display_name=m.display_name,
            provider_kind=m.provider_kind,  # type: ignore[arg-type]
            base_url=m.base_url,
            enabled=m.enabled,
            key_fingerprint=fingerprints.get(f"model:{m.id}"),
            sync_status=m.sync_status,  # type: ignore[arg-type]
            mock_response=m.mock_response,
        )
        for m in models
    ]


async def create_model(
    session: AsyncSession,
    ctx: TenantContext,
    *,
    litellm_model_name: str,
    display_name: str,
    provider_kind: str,
    base_url: str | None,
    api_key: str | None,
    settings: Settings,
    mock_response: str | None = None,
) -> Model:
    model = Model(
        litellm_model_name=litellm_model_name, display_name=display_name,
        provider_kind=provider_kind, base_url=base_url, mock_response=mock_response,
    )
    session.add(model)
    await session.flush()
    await record_audit(session, org_id=None, actor_id=ctx.user_id, action="model.created",
                       target_type="model", target_id=str(model.id))
    if api_key is not None:
        await secrets_service.set_secret(
            session, actor_id=ctx.user_id, name=f"model:{model.id}",
            value=api_key, settings=settings, commit=False,
        )
    await session.commit()
    return model


async def update_model(
    session: AsyncSession,
    ctx: TenantContext,
    model_id: UUID,
    *,
    display_name: str | None,
    base_url: str | None,
    enabled: bool | None,
    api_key: str | None,
    settings: Settings,
    mock_response: str | None = None,
) -> Model:
    model = await get_model(session, model_id)
    if display_name is not None:
        model.display_name = display_name
    if base_url is not None:
        model.base_url = base_url
    if enabled is not None:
        model.enabled = enabled
    if mock_response is not None:
        model.mock_response = mock_response
    await record_audit(session, org_id=None, actor_id=ctx.user_id, action="model.updated",
                       target_type="model", target_id=str(model.id))
    if api_key is not None:
        await secrets_service.set_secret(
            session, actor_id=ctx.user_id, name=f"model:{model.id}",
            value=api_key, settings=settings, commit=False,
        )
    await session.commit()
    return model


async def delete_model(
    session: AsyncSession, ctx: TenantContext, model_id: UUID, *, settings: Settings
) -> None:
    model = await get_model(session, model_id)
    await session.delete(model)
    await record_audit(session, org_id=None, actor_id=ctx.user_id, action="model.deleted",
                       target_type="model", target_id=str(model_id))
    try:
        await secrets_service.delete_secret(
            session, actor_id=ctx.user_id, name=f"model:{model_id}", commit=False
        )
    except NotFoundError:
        pass  # model never had an api_key set -- nothing to delete, not an error
    await session.commit()
