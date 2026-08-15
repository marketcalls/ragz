"""Superadmin-global provider settings: which document parser and reranker the
install uses, plus their encrypted API keys. Reads/writes app_settings (the
selections) and the secrets module (the keys, AES-256-GCM). The keys are
write-only — this module never returns a decrypted key, only *_key_set booleans.

Also owns the non-secret email delivery config (get/update_email_config):
same app_settings storage mechanism, "email_"-prefixed keys. Secret email
fields (smtp_password, ses_secret_key) are handled by modules/secrets, not
here -- see modules/email/schemas.py's EmailConfig for the full field list."""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from ragz.core.app_settings import get_app_setting, set_app_setting
from ragz.core.config import Settings
from ragz.core.errors import ConflictError
from ragz.modules.email.schemas import EmailConfig
from ragz.modules.models.schemas import ProviderSettingsOut, ProviderSettingsUpdate
from ragz.modules.secrets import service as secrets_service

_PARSER_KEY = "document_parser"
_RERANK_KEY = "rerank_provider"
_COHERE_MODEL_KEY = "cohere_rerank_model"
_COHERE_MODEL_DEFAULT = "rerank-v4.0-fast"
_WEB_SEARCH_PROVIDER_KEY = "web_search_provider"
_DEFAULT_CHUNK_METHOD_KEY = "default_chunk_method"
_LLAMA_SECRET = "llamaparse_api_key"  # noqa: S105 - a secret NAME, not a secret
_COHERE_SECRET = "cohere_api_key"  # noqa: S105 - a secret NAME, not a secret
_TAVILY_SECRET = "tavily"  # noqa: S105 - a secret NAME (matches web.TAVILY_SECRET_NAME)

# Non-secret email config (Task 1 of the email/password-reset plan): mirrors
# the document_parser/rerank_provider pattern above -- one app_settings row
# per field, "email_"-prefixed. Secret fields (smtp_password, ses_secret_key)
# are NEVER stored here; they go through modules/secrets in a later task.
_EMAIL_PROVIDER_KEY = "email_provider"
_EMAIL_FROM_EMAIL_KEY = "email_from_email"
_EMAIL_FROM_NAME_KEY = "email_from_name"
_EMAIL_SMTP_HOST_KEY = "email_smtp_host"
_EMAIL_SMTP_PORT_KEY = "email_smtp_port"
_EMAIL_SMTP_USE_TLS_KEY = "email_smtp_use_tls"
_EMAIL_SMTP_USERNAME_KEY = "email_smtp_username"
_EMAIL_SES_REGION_KEY = "email_ses_region"
_EMAIL_SES_ACCESS_KEY_ID_KEY = "email_ses_access_key_id"


async def get_provider_settings(session: AsyncSession) -> ProviderSettingsOut:
    parser = await get_app_setting(session, _PARSER_KEY) or "anydoc"
    rerank = await get_app_setting(session, _RERANK_KEY) or "local"
    cohere_model = await get_app_setting(session, _COHERE_MODEL_KEY) or _COHERE_MODEL_DEFAULT
    web_search = await get_app_setting(session, _WEB_SEARCH_PROVIDER_KEY) or "duckduckgo"
    default_chunk = await get_app_setting(session, _DEFAULT_CHUNK_METHOD_KEY) or "heading"
    present = await secrets_service.existing_secret_names(
        session, [_LLAMA_SECRET, _COHERE_SECRET, _TAVILY_SECRET]
    )
    return ProviderSettingsOut(
        document_parser=parser,
        rerank_provider=rerank,
        cohere_rerank_model=cohere_model,
        web_search_provider=web_search,
        default_chunk_method=default_chunk,
        llamaparse_key_set=_LLAMA_SECRET in present,
        cohere_key_set=_COHERE_SECRET in present,
        tavily_key_set=_TAVILY_SECRET in present,
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
    if patch.web_search_provider is not None:
        await set_app_setting(
            session, _WEB_SEARCH_PROVIDER_KEY, patch.web_search_provider, commit=False
        )
    if patch.default_chunk_method is not None:
        await set_app_setting(
            session, _DEFAULT_CHUNK_METHOD_KEY, patch.default_chunk_method, commit=False
        )
    for value, name in (
        (patch.llamaparse_api_key, _LLAMA_SECRET),
        (patch.cohere_api_key, _COHERE_SECRET),
        (patch.tavily_api_key, _TAVILY_SECRET),
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


async def get_email_config(session: AsyncSession) -> EmailConfig:
    """Read the non-secret email config. Unset fields fall back to
    `EmailConfig`'s own defaults, so an unconfigured install gets a
    well-defined empty state (provider="smtp", blank host/from-address)."""
    raw = {
        "provider": await get_app_setting(session, _EMAIL_PROVIDER_KEY),
        "from_email": await get_app_setting(session, _EMAIL_FROM_EMAIL_KEY),
        "from_name": await get_app_setting(session, _EMAIL_FROM_NAME_KEY),
        "smtp_host": await get_app_setting(session, _EMAIL_SMTP_HOST_KEY),
        "smtp_port": await get_app_setting(session, _EMAIL_SMTP_PORT_KEY),
        "smtp_use_tls": await get_app_setting(session, _EMAIL_SMTP_USE_TLS_KEY),
        "smtp_username": await get_app_setting(session, _EMAIL_SMTP_USERNAME_KEY),
        "ses_region": await get_app_setting(session, _EMAIL_SES_REGION_KEY),
        "ses_access_key_id": await get_app_setting(session, _EMAIL_SES_ACCESS_KEY_ID_KEY),
    }
    # Only pass through keys that are actually stored; missing keys fall back
    # to EmailConfig's field defaults via model_validate.
    present = {key: value for key, value in raw.items() if value is not None}
    return EmailConfig.model_validate(present)


async def update_email_config(
    session: AsyncSession, config: EmailConfig, *, commit: bool = True
) -> EmailConfig:
    """Write the non-secret email config (full replace -- `EmailConfig`
    already has sane defaults for every field, so there is no partial-patch
    ambiguity like `ProviderSettingsUpdate`'s optional secret fields)."""
    await set_app_setting(session, _EMAIL_PROVIDER_KEY, config.provider, commit=False)
    await set_app_setting(session, _EMAIL_FROM_EMAIL_KEY, config.from_email, commit=False)
    await set_app_setting(session, _EMAIL_FROM_NAME_KEY, config.from_name, commit=False)
    await set_app_setting(session, _EMAIL_SMTP_HOST_KEY, config.smtp_host, commit=False)
    await set_app_setting(
        session, _EMAIL_SMTP_PORT_KEY, str(config.smtp_port), commit=False
    )
    await set_app_setting(
        session,
        _EMAIL_SMTP_USE_TLS_KEY,
        "true" if config.smtp_use_tls else "false",
        commit=False,
    )
    await set_app_setting(session, _EMAIL_SMTP_USERNAME_KEY, config.smtp_username, commit=False)
    await set_app_setting(session, _EMAIL_SES_REGION_KEY, config.ses_region, commit=False)
    await set_app_setting(
        session, _EMAIL_SES_ACCESS_KEY_ID_KEY, config.ses_access_key_id, commit=False
    )
    if commit:
        await session.commit()
    else:
        await session.flush()
    return await get_email_config(session)
