from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ragz.core import net
from ragz.core.config import Settings
from ragz.core.errors import ConflictError, NotFoundError, SsrfBlocked
from ragz.modules.audit.models import AuditEvent
from ragz.modules.auth.models import User
from ragz.modules.models.models import LOCAL_EMBEDDING_MODEL_ID
from ragz.modules.models.service import (
    create_model,
    delete_model,
    list_enabled_models,
    list_models,
    resolve_model,
    to_model_out,
    update_model,
)
from ragz.modules.secrets.crypto import ensure_kek
from ragz.modules.secrets.models import Secret
from ragz.modules.tenancy.context import TenantContext


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    kek = tmp_path / "kek"
    ensure_kek(str(kek))
    return Settings(_env_file=None, kek_file=str(kek))


@pytest.fixture
def production_settings(tmp_path: Path) -> Settings:
    # sec RAGZ-PUB-11: mirrors tests/core/test_net.py's `production_settings`
    # fixture -- every field core/config.py's fail-closed validator checks
    # must be overridden so constructing this doesn't itself raise.
    kek = tmp_path / "kek_prod"
    ensure_kek(str(kek))
    kwargs: dict[str, object] = {
        "_env_file": None,
        "environment": "production",
        "api_key_pepper": "a-real-random-pepper-value",
        "database_url": "postgresql+asyncpg://ragz_prod:a-strong-16-char-pw@db.internal:5432/ragz",
        "minio_secret_key": "a-real-minio-secret",
        "litellm_master_key": "sk-a-real-litellm-master-key",
        "public_api_base_url": "https://api.example.com",
        "frontend_base_url": "https://app.example.com",
        "kek_file": str(kek),
    }
    return Settings(**kwargs)  # type: ignore[arg-type]


class _FakeLoop:
    """Stand-in for the running event loop -- see tests/core/test_net.py's
    identical helper; only `getaddrinfo` is used by `core/net.py`."""

    def __init__(self, result: list[tuple[object, ...]] | Exception) -> None:
        self._result = result

    async def getaddrinfo(self, host: str, port: object) -> list[tuple[object, ...]]:
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


def _dns_answers(*ips: str) -> list[tuple[object, ...]]:
    return [(None, None, None, "", (ip, 0)) for ip in ips]


def _patch_dns(
    monkeypatch: pytest.MonkeyPatch, result: list[tuple[object, ...]] | Exception
) -> None:
    monkeypatch.setattr(net.asyncio, "get_running_loop", lambda: _FakeLoop(result))


def super_ctx(user: User) -> TenantContext:
    return TenantContext(
        user_id=user.id, org_id=user.org_id, role="superadmin", workspace_ids=frozenset()
    )


async def test_create_stores_key_as_secret(
    session: AsyncSession, seeded_user: User, settings: Settings
) -> None:
    ctx = super_ctx(seeded_user)
    model = await create_model(
        session, ctx, litellm_model_name="gpt-4o-mini", display_name="GPT-4o mini",
        provider_kind="openai", base_url=None, api_key="sk-live-xyz", settings=settings,
    )
    secret = (
        await session.execute(select(Secret).where(Secret.name == f"model:{model.id}"))
    ).scalar_one()
    assert b"sk-live-xyz" not in secret.ciphertext
    assert model.sync_status == "pending"  # sync (Task 6) flips it
    actions = [e.action for e in (await session.execute(select(AuditEvent))).scalars()]
    assert "model.created" in actions and "secret.written" in actions


async def test_to_model_out_defaults_chat_modality(
    session: AsyncSession, seeded_user: User, settings: Settings
) -> None:
    """DOC-10: every pre-existing caller of create_model (no modality/dimension
    passed) keeps working unchanged, and serializes as modality="chat" with
    dimension/collection_name left None."""
    ctx = super_ctx(seeded_user)
    model = await create_model(
        session, ctx, litellm_model_name="gpt-4o-mini", display_name="GPT-4o mini",
        provider_kind="openai", base_url=None, api_key="sk-live-xyz", settings=settings,
    )
    out = (await to_model_out(session, [model]))[0]
    assert out.modality == "chat"
    assert out.dimension is None
    assert out.collection_name is None


async def test_update_and_disable(
    session: AsyncSession, seeded_user: User, settings: Settings
) -> None:
    ctx = super_ctx(seeded_user)
    model = await create_model(
        session, ctx, litellm_model_name="llama3", display_name="Llama 3",
        provider_kind="ollama", base_url="http://ollama:11434", api_key=None, settings=settings,
    )
    updated = await update_model(
        session, ctx, model.id, display_name="Llama 3 8B", base_url=None,
        enabled=False, api_key=None, settings=settings,
    )
    assert updated.display_name == "Llama 3 8B"
    assert updated.base_url == "http://ollama:11434"  # None = leave unchanged
    assert updated.enabled is False
    # tests/conftest.py's `engine` fixture seeds one globally-present enabled
    # embedding model (LOCAL_EMBEDDING_MODEL_ID, mirroring migration
    # d1e8f4a2b6c3) -- the just-disabled llama3 model must not be among the
    # enabled ones, but the seeded row still is.
    enabled = await list_enabled_models(session)
    assert [m.id for m in enabled] == [LOCAL_EMBEDDING_MODEL_ID]
    assert len(await list_models(session)) == 2


async def test_delete_removes_model_and_secret(
    session: AsyncSession, seeded_user: User, settings: Settings
) -> None:
    ctx = super_ctx(seeded_user)
    model = await create_model(
        session, ctx, litellm_model_name="gpt-4o", display_name="GPT-4o",
        provider_kind="openai", base_url=None, api_key="sk-1", settings=settings,
    )
    await delete_model(session, ctx, model.id, settings=settings)
    # Only the globally-seeded local embedding model remains (see
    # tests/conftest.py's `engine` fixture / migration d1e8f4a2b6c3).
    remaining = await list_models(session)
    assert [m.id for m in remaining] == [LOCAL_EMBEDDING_MODEL_ID]
    assert (
        await session.execute(select(Secret).where(Secret.name == f"model:{model.id}"))
    ).scalar_one_or_none() is None
    with pytest.raises(NotFoundError):
        await delete_model(session, ctx, uuid4(), settings=settings)


async def test_create_model_rolls_back_atomically_on_secret_failure(
    session: AsyncSession, seeded_user: User, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Model row + secret write must share one transaction: if the secret write fails
    (e.g. a KEK problem), the model row must not be left committed either."""
    ctx = super_ctx(seeded_user)

    async def boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("secrets backend unavailable")

    monkeypatch.setattr("ragz.modules.models.service.secrets_service.set_secret", boom)

    with pytest.raises(RuntimeError, match="secrets backend unavailable"):
        await create_model(
            session, ctx, litellm_model_name="gpt-4o-mini", display_name="GPT-4o mini",
            provider_kind="openai", base_url=None, api_key="sk-live-xyz", settings=settings,
        )

    await session.rollback()
    # Only the globally-seeded local embedding model remains -- the failed
    # create_model must not have left its row committed either.
    remaining = await list_models(session)
    assert [m.id for m in remaining] == [LOCAL_EMBEDDING_MODEL_ID]


async def test_resolve_model_order(
    session: AsyncSession, seeded_user: User, settings: Settings
) -> None:
    ctx = super_ctx(seeded_user)
    default = await create_model(
        session, ctx, litellm_model_name="llama3", display_name="Llama",
        provider_kind="ollama", base_url="http://ollama:11434", api_key=None,
        settings=settings,
    )
    override = await create_model(
        session, ctx, litellm_model_name="mistral", display_name="Mistral",
        provider_kind="ollama", base_url="http://ollama:11434", api_key=None,
        settings=settings,
    )
    # Explicit request wins over the workspace default.
    got = await resolve_model(session, requested_model_id=override.id,
                              default_model_id=default.id)
    assert got.id == override.id
    # No request -> workspace default.
    got = await resolve_model(session, requested_model_id=None, default_model_id=default.id)
    assert got.id == default.id
    # Unknown or disabled explicit request -> 404.
    with pytest.raises(NotFoundError):
        await resolve_model(session, requested_model_id=uuid4(), default_model_id=default.id)
    await update_model(session, ctx, override.id, display_name=None, base_url=None,
                       enabled=False, api_key=None, settings=settings)
    with pytest.raises(NotFoundError):
        await resolve_model(session, requested_model_id=override.id,
                            default_model_id=default.id)
    # Nothing resolves -> typed conflict.
    with pytest.raises(ConflictError):
        await resolve_model(session, requested_model_id=None, default_model_id=None)


async def test_create_model_blocks_ssrf_base_url_in_production(
    session: AsyncSession, seeded_user: User, production_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """sec RAGZ-PUB-11: the superadmin-settable base_url is forwarded to the
    LiteLLM proxy rather than dialed directly by Ragz, which is why the
    original SSRF guard skipped it -- defense in depth closes that gap: a
    base_url resolving to a blocked (private/loopback/metadata) address must
    be rejected in production/staging, with NO model row committed."""
    ctx = super_ctx(seeded_user)
    _patch_dns(monkeypatch, _dns_answers("169.254.169.254"))
    with pytest.raises(SsrfBlocked):
        await create_model(
            session, ctx, litellm_model_name="local", display_name="Local",
            provider_kind="openai_compatible", base_url="https://internal.example.com",
            api_key=None, settings=production_settings,
        )
    remaining = await list_models(session)
    assert [m.id for m in remaining] == [LOCAL_EMBEDDING_MODEL_ID]


async def test_create_model_allows_public_base_url_in_production(
    session: AsyncSession, seeded_user: User, production_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = super_ctx(seeded_user)
    _patch_dns(monkeypatch, _dns_answers("93.184.216.34"))
    model = await create_model(
        session, ctx, litellm_model_name="oai-compat", display_name="OAI Compat",
        provider_kind="openai_compatible", base_url="https://api.example.com",
        api_key=None, settings=production_settings,
    )
    assert model.base_url == "https://api.example.com"


async def test_create_model_skips_ssrf_check_when_base_url_empty(
    session: AsyncSession, seeded_user: User, production_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom() -> object:
        raise AssertionError("assert_public_url must not resolve DNS for an empty base_url")

    monkeypatch.setattr(net.asyncio, "get_running_loop", _boom)
    model = await create_model(
        session, ctx=super_ctx(seeded_user), litellm_model_name="gpt-4o-mini",
        display_name="GPT-4o mini", provider_kind="openai", base_url=None,
        api_key="sk-live-xyz", settings=production_settings,
    )
    assert model.base_url is None


async def test_update_model_blocks_ssrf_base_url_in_production(
    session: AsyncSession, seeded_user: User, settings: Settings,
    production_settings: Settings, monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = super_ctx(seeded_user)
    model = await create_model(
        session, ctx, litellm_model_name="llama3", display_name="Llama",
        provider_kind="ollama", base_url="http://ollama:11434", api_key=None,
        settings=settings,  # dev settings -- guard is a no-op at creation time
    )
    _patch_dns(monkeypatch, _dns_answers("10.0.0.5"))
    with pytest.raises(SsrfBlocked):
        await update_model(
            session, ctx, model.id, display_name=None,
            base_url="https://internal.example.com", enabled=None, api_key=None,
            settings=production_settings,
        )
    # The blocked base_url must not have been persisted onto the row.
    await session.refresh(model)
    assert model.base_url == "http://ollama:11434"


async def test_update_model_allows_public_base_url_in_production(
    session: AsyncSession, seeded_user: User, settings: Settings,
    production_settings: Settings, monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = super_ctx(seeded_user)
    model = await create_model(
        session, ctx, litellm_model_name="llama3", display_name="Llama",
        provider_kind="ollama", base_url="http://ollama:11434", api_key=None,
        settings=settings,
    )
    _patch_dns(monkeypatch, _dns_answers("93.184.216.34"))
    updated = await update_model(
        session, ctx, model.id, display_name=None,
        base_url="https://api.example.com", enabled=None, api_key=None,
        settings=production_settings,
    )
    assert updated.base_url == "https://api.example.com"
