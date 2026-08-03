"""Superadmin-global provider settings: which document parser and reranker the
install uses, plus their encrypted API keys. Reads/writes app_settings (the
selections) and the secrets module (the keys, AES-256-GCM). The keys are
write-only — this module never returns a decrypted key, only *_key_set booleans."""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from ragz.core.app_settings import get_app_setting, set_app_setting
from ragz.core.config import Settings
from ragz.core.errors import ConflictError
from ragz.modules.models.schemas import ProviderSettingsOut, ProviderSettingsUpdate
from ragz.modules.secrets import service as secrets_service

_PARSER_KEY = "document_parser"
_RERANK_KEY = "rerank_provider"
_COHERE_MODEL_KEY = "cohere_rerank_model"
_COHERE_MODEL_DEFAULT = "rerank-v4.0-fast"
_LLAMA_SECRET = "llamaparse_api_key"  # noqa: S105 - a secret NAME, not a secret
_COHERE_SECRET = "cohere_api_key"  # noqa: S105 - a secret NAME, not a secret


async def get_provider_settings(session: AsyncSession) -> ProviderSettingsOut:
    parser = await get_app_setting(session, _PARSER_KEY) or "docling"
    rerank = await get_app_setting(session, _RERANK_KEY) or "local"
    cohere_model = await get_app_setting(session, _COHERE_MODEL_KEY) or _COHERE_MODEL_DEFAULT
    present = await secrets_service.existing_secret_names(
        session, [_LLAMA_SECRET, _COHERE_SECRET]
    )
    return ProviderSettingsOut(
        document_parser=parser,
        rerank_provider=rerank,
        cohere_rerank_model=cohere_model,
        llamaparse_key_set=_LLAMA_SECRET in present,
        cohere_key_set=_COHERE_SECRET in present,
    )


async def update_provider_settings(
    session: AsyncSession,
    settings: Settings,
    *,
    actor_id: UUID | None,
    patch: ProviderSettingsUpdate,
) -> ProviderSettingsOut:
    if patch.document_parser is not None:
        await set_app_setting(session, _PARSER_KEY, patch.document_parser, commit=False)
    if patch.rerank_provider is not None:
        await set_app_setting(session, _RERANK_KEY, patch.rerank_provider, commit=False)
    if patch.cohere_rerank_model is not None:
        await set_app_setting(session, _COHERE_MODEL_KEY, patch.cohere_rerank_model, commit=False)
    for value, name in (
        (patch.llamaparse_api_key, _LLAMA_SECRET),
        (patch.cohere_api_key, _COHERE_SECRET),
    ):
        if value is not None:
            if not value.strip():
                raise ConflictError(f"{name} may not be blank")
            await secrets_service.set_secret(
                session, actor_id=actor_id, name=name, value=value,
                settings=settings, commit=False,
            )
    await session.commit()
    return await get_provider_settings(session)
