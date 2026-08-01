import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ragz.core.app_settings import get_app_setting
from ragz.modules.audit.models import AuditEvent
from ragz.modules.auth.models import User
from ragz.modules.auth.oidc import OIDC_CLIENT_ID_KEY, OIDC_ISSUER_KEY


async def auth(client: httpx.AsyncClient, email: str) -> dict[str, str]:
    r = await client.post("/api/v1/auth/login", json={"email": email, "password": "pw123456"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def test_sso_config_roundtrip_never_redisplays_secret(
    client: httpx.AsyncClient, seeded_superadmin: User
) -> None:
    h = await auth(client, "root@platform.example")
    r = await client.get("/api/v1/admin/sso", headers=h)
    assert r.json() == {"issuer": None, "client_id": None, "client_secret_set": False}

    r = await client.put("/api/v1/admin/sso", headers=h, json={
        "issuer": "https://idp.example.com", "client_id": "ragz",
        "client_secret": "s3cret-value",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["client_secret_set"] is True
    assert "s3cret-value" not in r.text  # SEC-2: write-only

    # PUT without a secret keeps the stored one
    r = await client.put("/api/v1/admin/sso", headers=h, json={
        "issuer": "https://idp.example.com", "client_id": "ragz2",
    })
    assert r.json() == {"issuer": "https://idp.example.com", "client_id": "ragz2",
                        "client_secret_set": True}


async def test_org_sso_domains(
    client: httpx.AsyncClient, seeded_superadmin: User
) -> None:
    h = await auth(client, "root@platform.example")
    orgs = (await client.get("/api/v1/admin/orgs", headers=h)).json()
    org_id = orgs[0]["id"]
    r = await client.put(f"/api/v1/admin/orgs/{org_id}/sso-domains",
                         headers=h, json={"domains": ["Acme.COM", "acme.io"]})
    assert r.status_code == 200
    assert r.json()["sso_domains"] == ["acme.com", "acme.io"]  # normalized lowercase


async def test_sso_admin_requires_superadmin(
    client: httpx.AsyncClient, seeded_user: User
) -> None:
    h = await auth(client, "a@acme.com")  # org admin, not superadmin
    assert (await client.get("/api/v1/admin/sso", headers=h)).status_code == 403


async def test_put_sso_rolls_back_atomically_on_secret_failure(
    client: httpx.AsyncClient,
    seeded_superadmin: User,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Review finding: the old route committed the settings write, the secret write,
    and the audit record as three independent transactions, so a failure between
    them (e.g. the secret write raising) could leave issuer/client_id persisted with
    no audit record. The route now composes one service call
    (modules.auth.oidc.set_sso_config) that commits exactly once."""
    h = await auth(client, "root@platform.example")

    async def boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("secrets backend unavailable")

    monkeypatch.setattr("ragz.modules.auth.oidc.secrets_service.set_secret", boom)

    # The default `client` fixture's ASGITransport re-raises app exceptions (see
    # tests/api/test_error_handlers.py's crashy_client for the same caveat); a real
    # deployment's global handler still turns this into a bare 500 for the caller.
    with pytest.raises(RuntimeError, match="secrets backend unavailable"):
        await client.put("/api/v1/admin/sso", headers=h, json={
            "issuer": "https://idp.example.com", "client_id": "ragz",
            "client_secret": "s3cret-value",
        })

    assert await get_app_setting(session, OIDC_ISSUER_KEY) is None
    assert await get_app_setting(session, OIDC_CLIENT_ID_KEY) is None
    actions = [e.action for e in (await session.execute(select(AuditEvent))).scalars()]
    assert "sso.config_changed" not in actions

    # Confirms the config really is unset end-to-end, not just at the DB layer.
    after = (await client.get("/api/v1/admin/sso", headers=h)).json()
    assert after == {"issuer": None, "client_id": None, "client_secret_set": False}
