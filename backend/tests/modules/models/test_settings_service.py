from pathlib import Path

import pytest

from ragz.core.config import Settings
from ragz.core.errors import ConflictError
from ragz.modules.auth.models import User
from ragz.modules.models import settings_service
from ragz.modules.models.schemas import ProviderSettingsUpdate
from ragz.modules.secrets.crypto import ensure_kek


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    kek = tmp_path / "kek"
    ensure_kek(str(kek))
    return Settings(_env_file=None, kek_file=str(kek))


async def test_defaults_when_nothing_set(session, settings) -> None:
    out = await settings_service.get_provider_settings(session)
    assert out.document_parser == "anydoc"
    assert out.rerank_provider == "local"
    assert out.cohere_rerank_model == "rerank-v4.0-fast"
    assert out.llamaparse_key_set is False
    assert out.cohere_key_set is False


async def test_document_parser_defaults_to_anydoc(session, settings) -> None:
    out = await settings_service.get_provider_settings(session)
    assert out.document_parser == "anydoc"


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


async def test_empty_key_string_rejected(session, settings, seeded_user: User) -> None:
    with pytest.raises(ConflictError):
        await settings_service.update_provider_settings(
            session, settings, actor_id=seeded_user.id,
            patch=ProviderSettingsUpdate(cohere_api_key="  "),
        )
