from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from raghub.core.config import Settings
from raghub.core.errors import ConflictError, NotFoundError
from raghub.modules.audit.models import AuditEvent
from raghub.modules.auth.models import User
from raghub.modules.models.service import (
    create_model,
    delete_model,
    list_enabled_models,
    list_models,
    resolve_model,
    update_model,
)
from raghub.modules.secrets.crypto import ensure_kek
from raghub.modules.secrets.models import Secret
from raghub.modules.tenancy.context import TenantContext


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    kek = tmp_path / "kek"
    ensure_kek(str(kek))
    return Settings(_env_file=None, kek_file=str(kek))


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
    assert await list_enabled_models(session) == []
    assert len(await list_models(session)) == 1


async def test_delete_removes_model_and_secret(
    session: AsyncSession, seeded_user: User, settings: Settings
) -> None:
    ctx = super_ctx(seeded_user)
    model = await create_model(
        session, ctx, litellm_model_name="gpt-4o", display_name="GPT-4o",
        provider_kind="openai", base_url=None, api_key="sk-1", settings=settings,
    )
    await delete_model(session, ctx, model.id, settings=settings)
    assert await list_models(session) == []
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

    monkeypatch.setattr("raghub.modules.models.service.secrets_service.set_secret", boom)

    with pytest.raises(RuntimeError, match="secrets backend unavailable"):
        await create_model(
            session, ctx, litellm_model_name="gpt-4o-mini", display_name="GPT-4o mini",
            provider_kind="openai", base_url=None, api_key="sk-live-xyz", settings=settings,
        )

    await session.rollback()
    assert await list_models(session) == []


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
