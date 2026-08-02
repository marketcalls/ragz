from uuid import UUID

from sqlalchemy import select, true, update
from sqlalchemy.ext.asyncio import AsyncSession

from ragz.core.config import Settings
from ragz.core.errors import ConflictError, NotFoundError
from ragz.modules.audit.service import record_audit
from ragz.modules.models.models import Model
from ragz.modules.models.schemas import ModelOut
from ragz.modules.models.utility import get_utility_model as resolve_utility_model
from ragz.modules.secrets import service as secrets_service
from ragz.modules.tenancy.context import TenantContext

# `resolve_utility_model` is a deliberate re-export (Plan K Tasks 6/7/9 call
# `models_service.resolve_utility_model(session)`; the single real
# implementation stays in modules/models/utility.py per the single-seam
# convention -- see that module's docstring). Listed in __all__ so linters
# don't flag the aliased import as unused.
__all__ = ["resolve_utility_model"]


async def get_model(session: AsyncSession, model_id: UUID) -> Model:
    model = (
        await session.execute(select(Model).where(Model.id == model_id))
    ).scalar_one_or_none()
    if model is None:
        raise NotFoundError("model not found")
    return model


async def list_models(session: AsyncSession) -> list[Model]:
    return list((await session.execute(select(Model).order_by(Model.created_at))).scalars())


async def list_enabled_models(session: AsyncSession, modality: str | None = None) -> list[Model]:
    stmt = select(Model).where(Model.enabled == true()).order_by(Model.created_at)
    if modality is not None:
        stmt = stmt.where(Model.modality == modality)
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
            tools_unreliable=m.tools_unreliable,
            supports_reasoning=m.supports_reasoning,
            default_reasoning_effort=m.default_reasoning_effort,  # type: ignore[arg-type]
            supports_vision=m.supports_vision,
            is_utility=m.is_utility,
            modality=m.modality,  # type: ignore[arg-type]
            dimension=m.dimension,
            collection_name=m.collection_name,
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
    tools_unreliable: bool = False,
    supports_reasoning: bool = False,
    default_reasoning_effort: str = "off",
    supports_vision: bool = False,
    modality: str = "chat",
    dimension: int | None = None,
) -> Model:
    model = Model(
        litellm_model_name=litellm_model_name, display_name=display_name,
        provider_kind=provider_kind, base_url=base_url, mock_response=mock_response,
        tools_unreliable=tools_unreliable, supports_reasoning=supports_reasoning,
        default_reasoning_effort=default_reasoning_effort, supports_vision=supports_vision,
        modality=modality, dimension=dimension,
    )
    session.add(model)
    await session.flush()  # assigns model.id -- collection_name needs it
    if modality == "embedding":
        model.collection_name = f"chunks_{model.id.hex}"
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
    tools_unreliable: bool | None = None,
    supports_reasoning: bool | None = None,
    default_reasoning_effort: str | None = None,
    supports_vision: bool | None = None,
    is_utility: bool | None = None,
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
    if tools_unreliable is not None:
        model.tools_unreliable = tools_unreliable
    if supports_reasoning is not None:
        model.supports_reasoning = supports_reasoning
    if default_reasoning_effort is not None:
        model.default_reasoning_effort = default_reasoning_effort
    if supports_vision is not None:
        model.supports_vision = supports_vision
    if is_utility is not None:
        if is_utility:
            # "exactly one" (design D5): clear every OTHER row in the same
            # transaction before setting this one, so two concurrent PATCHes
            # can never both land True — last committer wins, cleanly.
            await session.execute(
                update(Model).where(Model.id != model_id).values(is_utility=False)
            )
            model.is_utility = True
        else:
            model.is_utility = False
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
    if model.provider_kind == "tei":
        raise ConflictError("the built-in local embedding model cannot be deleted")
    if model.modality == "embedding":
        # Local import: tenancy.service already imports this module at
        # module scope (set_default_model), so importing tenancy.service
        # here at module scope would be a real circular import -- same
        # sanctioned shape as tenancy.service's own local import of
        # worker.tasks (see that module's update_retrieval_settings).
        from ragz.modules.tenancy.service import workspace_uses_embedding_model

        if await workspace_uses_embedding_model(session, model_id):
            raise ConflictError("embedding model is in use by at least one workspace")
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
