from pathlib import Path
from uuid import uuid4

import pytest

from ragz.core.app_settings import set_app_setting
from ragz.core.config import Settings
from ragz.core.errors import ConflictError, NotFoundError
from ragz.modules.auth.models import User
from ragz.modules.models import settings_service
from ragz.modules.models.models import Model
from ragz.modules.models.schemas import ProviderSettingsUpdate
from ragz.modules.secrets.crypto import ensure_kek


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    kek = tmp_path / "kek"
    ensure_kek(str(kek))
    return Settings(_env_file=None, kek_file=str(kek))


async def test_defaults_when_nothing_set(session, settings) -> None:
    out = await settings_service.get_provider_settings(session)
    assert out.document_parser == "liteparse"
    assert out.rerank_provider == "local"
    assert out.cohere_rerank_model == "rerank-v4.0-fast"
    assert out.web_search_provider == "duckduckgo"
    # Full page-content enrichment defaults ON when unset.
    assert out.web_search_full_content is True
    assert out.default_chunk_method == "heading"
    assert out.llamaparse_key_set is False
    assert out.cohere_key_set is False
    assert out.tavily_key_set is False
    assert out.generative_ui_images == "off"
    # Global generative-UI gate defaults ON when unset.
    assert out.generative_ui_enabled is True


async def test_web_search_provider_and_tavily_key_roundtrip(
    session, settings, seeded_user: User
) -> None:
    out = await settings_service.update_provider_settings(
        session, settings, actor_id=seeded_user.id,
        patch=ProviderSettingsUpdate(
            web_search_provider="tavily", tavily_api_key="tvly-secret",
        ),
    )
    assert out.web_search_provider == "tavily"
    assert out.tavily_key_set is True
    # write-only: the key itself is never echoed back on the Out schema.
    assert not hasattr(out, "tavily_api_key")


async def test_web_search_full_content_roundtrip(session, settings, seeded_user: User) -> None:
    # Default ON; turning it OFF round-trips, and turning it back ON restores.
    out = await settings_service.update_provider_settings(
        session, settings, actor_id=seeded_user.id,
        patch=ProviderSettingsUpdate(web_search_full_content=False),
    )
    assert out.web_search_full_content is False
    out = await settings_service.get_provider_settings(session)
    assert out.web_search_full_content is False
    out = await settings_service.update_provider_settings(
        session, settings, actor_id=seeded_user.id,
        patch=ProviderSettingsUpdate(web_search_full_content=True),
    )
    assert out.web_search_full_content is True


async def test_default_chunk_method_roundtrip(session, settings, seeded_user: User) -> None:
    out = await settings_service.update_provider_settings(
        session, settings, actor_id=seeded_user.id,
        patch=ProviderSettingsUpdate(default_chunk_method="page"),
    )
    assert out.default_chunk_method == "page"


async def test_empty_tavily_key_rejected(session, settings, seeded_user: User) -> None:
    with pytest.raises(ConflictError):
        await settings_service.update_provider_settings(
            session, settings, actor_id=seeded_user.id,
            patch=ProviderSettingsUpdate(tavily_api_key="   "),
        )


async def test_document_parser_defaults_to_liteparse(session, settings) -> None:
    out = await settings_service.get_provider_settings(session)
    assert out.document_parser == "liteparse"


async def test_update_accepts_anydoc(session, settings, seeded_user: User) -> None:
    out = await settings_service.update_provider_settings(
        session, settings, actor_id=seeded_user.id,
        patch=ProviderSettingsUpdate(document_parser="anydoc"),
    )
    assert out.document_parser == "anydoc"


async def test_update_selections_and_keys_roundtrip(session, settings, seeded_user: User) -> None:
    out = await settings_service.update_provider_settings(
        session, settings, actor_id=seeded_user.id,
        patch=ProviderSettingsUpdate(
            document_parser="llamaparse", rerank_provider="cohere",
            llamaparse_api_key="llx-secret", cohere_api_key="ck-secret",
        ),
    )
    assert out.document_parser == "llamaparse"
    assert out.rerank_provider == "cohere"
    assert out.llamaparse_key_set is True
    assert out.cohere_key_set is True


async def test_omitted_key_leaves_existing_untouched(session, settings, seeded_user: User) -> None:
    await settings_service.update_provider_settings(
        session, settings, actor_id=seeded_user.id,
        patch=ProviderSettingsUpdate(cohere_api_key="ck-1"),
    )
    # second update omits the key -> stays set
    out = await settings_service.update_provider_settings(
        session, settings, actor_id=seeded_user.id,
        patch=ProviderSettingsUpdate(rerank_provider="cohere"),
    )
    assert out.cohere_key_set is True


async def test_generative_ui_images_roundtrip(session, settings, seeded_user: User) -> None:
    out = await settings_service.update_provider_settings(
        session, settings, actor_id=seeded_user.id,
        patch=ProviderSettingsUpdate(generative_ui_images="web_results"),
    )
    assert out.generative_ui_images == "web_results"


async def test_generative_ui_enabled_roundtrip(session, settings, seeded_user: User) -> None:
    # Default ON; turning it OFF round-trips, and turning it back ON restores.
    out = await settings_service.update_provider_settings(
        session, settings, actor_id=seeded_user.id,
        patch=ProviderSettingsUpdate(generative_ui_enabled=False),
    )
    assert out.generative_ui_enabled is False
    out = await settings_service.get_provider_settings(session)
    assert out.generative_ui_enabled is False
    out = await settings_service.update_provider_settings(
        session, settings, actor_id=seeded_user.id,
        patch=ProviderSettingsUpdate(generative_ui_enabled=True),
    )
    assert out.generative_ui_enabled is True


async def test_empty_key_string_rejected(session, settings, seeded_user: User) -> None:
    with pytest.raises(ConflictError):
        await settings_service.update_provider_settings(
            session, settings, actor_id=seeded_user.id,
            patch=ProviderSettingsUpdate(cohere_api_key="  "),
        )


async def test_default_embedding_model_unset_is_none(session, settings) -> None:
    """Unset means "use the column default" (the built-in local TEI model), so
    installs that never touch this setting keep their existing behaviour."""
    out = await settings_service.get_provider_settings(session)
    assert out.default_embedding_model_id is None


async def test_default_embedding_model_roundtrip(
    session, settings, seeded_user: User
) -> None:
    embedder = Model(
        litellm_model_name="text-embedding-3-large", display_name="OpenAI Large",
        provider_kind="openai", modality="embedding", dimension=3072, enabled=True,
    )
    session.add(embedder)
    await session.flush()

    out = await settings_service.update_provider_settings(
        session, settings, actor_id=seeded_user.id,
        patch=ProviderSettingsUpdate(default_embedding_model_id=embedder.id),
    )
    assert out.default_embedding_model_id == embedder.id
    out = await settings_service.get_provider_settings(session)
    assert out.default_embedding_model_id == embedder.id
    assert await settings_service.get_default_embedding_model_id(session) == embedder.id


async def test_default_embedding_model_rejects_chat_model(
    session, settings, seeded_user: User
) -> None:
    """A chat model here would be inherited by every later workspace and only
    surface as a failed ingestion, so it must be refused at write time."""
    chat = Model(
        litellm_model_name="gpt-4o", display_name="GPT-4o",
        provider_kind="openai", modality="chat", enabled=True,
    )
    session.add(chat)
    await session.flush()

    with pytest.raises(ConflictError):
        await settings_service.update_provider_settings(
            session, settings, actor_id=seeded_user.id,
            patch=ProviderSettingsUpdate(default_embedding_model_id=chat.id),
        )


async def test_default_embedding_model_rejects_unknown_id(
    session, settings, seeded_user: User
) -> None:
    with pytest.raises(NotFoundError):
        await settings_service.update_provider_settings(
            session, settings, actor_id=seeded_user.id,
            patch=ProviderSettingsUpdate(default_embedding_model_id=uuid4()),
        )


async def test_corrupt_stored_default_reads_as_unset(session) -> None:
    """Read on the workspace-creation path: a non-UUID row must degrade to the
    column default, never make creating a workspace impossible."""
    await set_app_setting(session, "default_embedding_model_id", "not-a-uuid")
    assert await settings_service.get_default_embedding_model_id(session) is None
