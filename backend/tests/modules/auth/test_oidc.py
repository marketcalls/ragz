import json
from pathlib import Path

import httpx
import pytest
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ragz.core import net
from ragz.core.app_settings import get_app_setting, set_app_setting
from ragz.core.config import Settings
from ragz.core.errors import SsrfBlocked
from ragz.modules.audit.models import AuditEvent
from ragz.modules.auth import oidc
from ragz.modules.auth.oidc import (
    OIDC_CLIENT_ID_KEY,
    OIDC_ISSUER_KEY,
    OIDC_SECRET_NAME,
    OidcProvider,
    get_sso_config,
    load_provider,
    set_sso_config,
)
from ragz.modules.secrets import service as secrets_service
from ragz.modules.secrets.crypto import ensure_kek


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


# -- sec RAGZ-PUB-11: SSRF egress guard on OIDC discovery/token/JWKS -------


class _FakeLoop:
    """Fakes `asyncio.get_running_loop().getaddrinfo` for `core/net.py`'s
    DNS resolution step, so these guard tests don't touch the network."""

    def __init__(self, ip: str) -> None:
        self._ip = ip

    async def getaddrinfo(self, host: str, port: object) -> list[tuple[object, ...]]:
        return [(None, None, None, "", (self._ip, 0))]


def _fail_if_called(request: httpx.Request) -> httpx.Response:
    raise AssertionError(f"outbound request must not happen once blocked: {request.url}")


@pytest.fixture
def production_settings(tmp_path: Path) -> Settings:
    # Mirrors tests/core/test_production_config.py's `safe_kwargs`.
    kek = tmp_path / "kek"
    ensure_kek(str(kek))
    # Built as a dict (not literal kwargs) so ruff's S106 hardcoded-password
    # heuristic doesn't fire on these deliberately-fake test values.
    kwargs: dict[str, object] = {
        "_env_file": None,
        "environment": "production",
        "api_key_pepper": "a-real-random-pepper-value",
        "database_url": "postgresql+asyncpg://ragz_prod:s3cret-pw@db.internal:5432/ragz",
        "minio_secret_key": "a-real-minio-secret",
        "litellm_master_key": "sk-a-real-litellm-master-key",
        "public_api_base_url": "https://api.example.com",
        "frontend_base_url": "https://app.example.com",
        "kek_file": str(kek),
    }
    return Settings(**kwargs)  # type: ignore[arg-type]


async def test_load_provider_blocks_issuer_resolving_to_internal_ip_in_production(
    session: AsyncSession, production_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    await set_app_setting(session, OIDC_ISSUER_KEY, "https://sso.internal.corp")
    await set_app_setting(session, OIDC_CLIENT_ID_KEY, "ragz")
    monkeypatch.setattr(net.asyncio, "get_running_loop", lambda: _FakeLoop("10.0.0.9"))
    with pytest.raises(SsrfBlocked):
        await load_provider(
            session, settings=production_settings, transport=httpx.MockTransport(_fail_if_called)
        )


async def test_load_provider_allows_public_issuer_in_production(
    session: AsyncSession, production_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    await set_app_setting(session, OIDC_ISSUER_KEY, "https://idp.example.com")
    await set_app_setting(session, OIDC_CLIENT_ID_KEY, "ragz")
    monkeypatch.setattr(net.asyncio, "get_running_loop", lambda: _FakeLoop("93.184.216.34"))

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "issuer": "https://idp.example.com",
            "authorization_endpoint": "https://idp.example.com/authorize",
            "token_endpoint": "https://idp.example.com/token",
            "jwks_uri": "https://idp.example.com/jwks",
        })

    provider = await load_provider(
        session, settings=production_settings, transport=httpx.MockTransport(handler)
    )
    assert provider is not None
    assert provider.token_endpoint == "https://idp.example.com/token"  # noqa: S105


async def test_load_provider_is_noop_guard_outside_production(
    session: AsyncSession, test_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """dev/test stay permissive even for a discovery URL that would resolve
    to a blocked address -- the guard must not even attempt DNS resolution
    (the fake loop always raises), proving the environment check short-
    circuits before `_assert_resolves_public`."""
    await set_app_setting(session, OIDC_ISSUER_KEY, "https://sso.internal.corp")
    await set_app_setting(session, OIDC_CLIENT_ID_KEY, "ragz")

    class _RaisingLoop:
        async def getaddrinfo(self, host: str, port: object) -> list[tuple[object, ...]]:
            raise AssertionError("DNS must not be resolved outside production/staging")

    monkeypatch.setattr(net.asyncio, "get_running_loop", lambda: _RaisingLoop())

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "issuer": "https://sso.internal.corp",
            "authorization_endpoint": "https://sso.internal.corp/authorize",
            "token_endpoint": "https://sso.internal.corp/token",
            "jwks_uri": "https://sso.internal.corp/jwks",
        })

    provider = await load_provider(
        session, settings=test_settings, transport=httpx.MockTransport(handler)
    )
    assert provider is not None


async def test_complete_login_blocks_token_endpoint_resolving_to_internal_ip_in_production(
    session: AsyncSession,
    redis_client: Redis,
    production_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The discovery document's `token_endpoint`/`jwks_uri` are guarded
    again right before `complete_login` dials them (in case the document
    itself points somewhere the issuer URL didn't) -- a blocked target must
    raise before either the token POST or the JWKS GET reaches the network."""
    await secrets_service.set_secret(
        session, actor_id=None, name=OIDC_SECRET_NAME, value="cs-1", settings=production_settings
    )
    preauth_token = "preauth-raw-token"  # noqa: S105 - test fixture value
    state = "state-1"
    await redis_client.set(
        f"oidc:state:{state}",
        json.dumps({
            "verifier": "verifier-1",
            "nonce": "nonce-1",
            "preauth_hash": oidc._hash_preauth_token(preauth_token),  # noqa: SLF001
        }),
        ex=600,
    )
    provider = OidcProvider(
        issuer="https://idp.example.com",
        client_id="ragz",
        authorization_endpoint="https://idp.example.com/authorize",
        token_endpoint="https://internal.corp/token",  # noqa: S106
        jwks_uri="https://internal.corp/jwks",
    )
    monkeypatch.setattr(net.asyncio, "get_running_loop", lambda: _FakeLoop("10.0.0.9"))

    with pytest.raises(SsrfBlocked):
        await oidc.complete_login(
            session, provider, redis_client,
            code="auth-code", state=state, redirect_uri="https://api.example.com/callback",
            settings=production_settings, preauth_cookie=preauth_token,
            transport=httpx.MockTransport(_fail_if_called),
        )
