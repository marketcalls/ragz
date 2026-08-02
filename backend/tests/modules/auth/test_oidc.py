import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ragz.core.app_settings import get_app_setting
from ragz.core.config import Settings
from ragz.modules.audit.models import AuditEvent
from ragz.modules.auth.oidc import (
    OIDC_CLIENT_ID_KEY,
    OIDC_ISSUER_KEY,
    get_sso_config,
    set_sso_config,
)


async def test_set_sso_config_persists_issuer_client_id_and_secret_presence(
    session: AsyncSession, test_settings: Settings
) -> None:
    config = await set_sso_config(
        session, actor_id=None, issuer="https://idp.example.com", client_id="ragz",
        client_secret="s3cret-value", settings=test_settings,  # noqa: S106
    )
    assert config.issuer == "https://idp.example.com"
    assert config.client_id == "ragz"
    assert config.client_secret_set is True

    reloaded = await get_sso_config(session)
    assert reloaded == config


async def test_set_sso_config_rolls_back_atomically_on_secret_failure(
    session: AsyncSession, test_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The issuer/client_id settings, the secret write, and the audit record must
    share one transaction: if the secret write fails mid-flow, NONE of the three
    may be left committed (review finding: prior route committed each write
    independently, so a partial failure could persist issuer/client_id with no
    audit record)."""

    async def boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("secrets backend unavailable")

    monkeypatch.setattr("ragz.modules.auth.oidc.secrets_service.set_secret", boom)

    with pytest.raises(RuntimeError, match="secrets backend unavailable"):
        await set_sso_config(
            session, actor_id=None, issuer="https://idp.example.com", client_id="ragz",
            client_secret="s3cret-value", settings=test_settings,  # noqa: S106
        )

    await session.rollback()
    assert await get_app_setting(session, OIDC_ISSUER_KEY) is None
    assert await get_app_setting(session, OIDC_CLIENT_ID_KEY) is None
    actions = [e.action for e in (await session.execute(select(AuditEvent))).scalars()]
    assert "sso.config_changed" not in actions
