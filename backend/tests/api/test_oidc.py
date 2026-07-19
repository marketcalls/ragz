"""OIDC flow against a MockTransport IdP (the sanctioned httpx-layer mock,
per the modules/chat/llm.py precedent — see Global Constraints for why dex is
a manual smoke, not a CI container)."""

import time
from collections.abc import AsyncIterator
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from authlib.jose import JsonWebKey, jwt
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from raghub.api.app import create_app
from raghub.core.config import Settings, get_settings
from raghub.core.db import build_session_factory
from raghub.modules.auth.models import User
from raghub.modules.tenancy.models import Organization

ISSUER = "https://idp.example.com"
KEY = JsonWebKey.generate_key("RSA", 2048, is_private=True)
# A second, entirely unrelated key: JWKS never advertises it. Used to mint an
# ID token with a signature that cannot verify against the published JWKS,
# simulating a token forged by (or replayed from) a different issuer/key.
OTHER_KEY = JsonWebKey.generate_key("RSA", 2048, is_private=True)
_nonce_box: dict[str, str] = {}


def _idp_handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path == "/.well-known/openid-configuration":
        return httpx.Response(200, json={
            "issuer": ISSUER,
            "authorization_endpoint": f"{ISSUER}/authorize",
            "token_endpoint": f"{ISSUER}/token",
            "jwks_uri": f"{ISSUER}/jwks",
        })
    if path == "/jwks":
        return httpx.Response(200, json={"keys": [KEY.as_dict(private=False)]})
    if path == "/token":
        form = parse_qs(request.content.decode())
        assert form["grant_type"] == ["authorization_code"]
        assert "code_verifier" in form and "client_secret" in form
        now = int(time.time())
        signing_key = _nonce_box.get("key_override", KEY)
        id_token = jwt.encode(
            {"alg": "RS256"},
            {"iss": _nonce_box.get("iss_override", ISSUER),
             "aud": _nonce_box.get("aud_override", "raghub"), "sub": "idp-user-1",
             "email": _nonce_box.get("email_override", "New.Hire@acme.com"),
             "email_verified": True,
             "nonce": _nonce_box["nonce"], "iat": now, "exp": now + 300},
            signing_key,
        ).decode()
        return httpx.Response(200, json={"id_token": id_token, "access_token": "at",
                                         "token_type": "Bearer"})
    return httpx.Response(404)


@pytest.fixture
async def sso_client(
    engine: AsyncEngine, redis_client: Redis, test_settings: Settings, session: AsyncSession
) -> AsyncIterator[httpx.AsyncClient]:
    org = Organization(name="Acme", sso_domains=["acme.com"])
    session.add(org)
    await session.commit()
    from raghub.core.app_settings import set_app_setting
    from raghub.modules.auth.oidc import OIDC_CLIENT_ID_KEY, OIDC_ISSUER_KEY, OIDC_SECRET_NAME
    from raghub.modules.secrets import service as secrets_service

    await set_app_setting(session, OIDC_ISSUER_KEY, ISSUER)
    await set_app_setting(session, OIDC_CLIENT_ID_KEY, "raghub")
    await secrets_service.set_secret(session, actor_id=None, name=OIDC_SECRET_NAME,
                                     value="cs-1", settings=test_settings)

    app = create_app(
        session_factory=build_session_factory(engine),
        redis_client=redis_client,
        litellm_transport=httpx.MockTransport(
            lambda r: httpx.Response(200, json={"data": []})
        ),
        oidc_transport=httpx.MockTransport(_idp_handler),
    )
    app.dependency_overrides[get_settings] = lambda: test_settings
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_status_reports_enabled(sso_client: httpx.AsyncClient) -> None:
    r = await sso_client.get("/api/v1/auth/oidc/status")
    assert r.json() == {"enabled": True}


async def test_full_flow_jit_provisions_and_sets_refresh_cookie(
    sso_client: httpx.AsyncClient, session: AsyncSession
) -> None:
    r = await sso_client.get("/api/v1/auth/oidc/login", follow_redirects=False)
    assert r.status_code == 302
    q = parse_qs(urlparse(r.headers["location"]).query)
    assert q["code_challenge_method"] == ["S256"]
    _nonce_box["nonce"] = q["nonce"][0]

    r2 = await sso_client.get(
        f"/api/v1/auth/oidc/callback?code=abc&state={q['state'][0]}",
        follow_redirects=False,
    )
    assert r2.status_code == 302
    assert "refresh_token" in r2.cookies

    user = (
        await session.execute(select(User).where(User.email == "new.hire@acme.com"))
    ).scalar_one()
    assert user.role == "user" and user.active

    # cookie works with the existing refresh machinery
    r3 = await sso_client.post("/api/v1/auth/refresh",
                               cookies={"refresh_token": r2.cookies["refresh_token"]})
    assert r3.status_code == 200 and r3.json()["access_token"]


def _assert_sso_error_redirect(r: httpx.Response, test_settings: Settings) -> None:
    """Contract C7: callback failures are a top-level browser navigation, so
    they redirect back to the login page (never raw problem+json) -- and the
    failure reason never leaks into the redirect URL, only into structlog."""
    assert r.status_code == 302
    assert r.headers["location"] == f"{test_settings.frontend_base_url}/login?sso_error=1"


async def test_state_is_single_use(
    sso_client: httpx.AsyncClient, test_settings: Settings
) -> None:
    r = await sso_client.get("/api/v1/auth/oidc/login", follow_redirects=False)
    q = parse_qs(urlparse(r.headers["location"]).query)
    _nonce_box["nonce"] = q["nonce"][0]
    state = q["state"][0]
    assert (
        await sso_client.get(f"/api/v1/auth/oidc/callback?code=abc&state={state}",
                             follow_redirects=False)
    ).status_code == 302
    _assert_sso_error_redirect(
        await sso_client.get(f"/api/v1/auth/oidc/callback?code=abc&state={state}",
                             follow_redirects=False),
        test_settings,
    )  # replayed state


async def test_unknown_state_redirects_to_login(
    sso_client: httpx.AsyncClient, test_settings: Settings
) -> None:
    """An expired/never-issued state must not render problem+json to the
    browser -- it lands back on the login page with a generic error flag."""
    _assert_sso_error_redirect(
        await sso_client.get(
            "/api/v1/auth/oidc/callback?code=abc&state=totally-unknown-state",
            follow_redirects=False,
        ),
        test_settings,
    )


async def test_unlisted_domain_rejected(
    sso_client: httpx.AsyncClient, test_settings: Settings
) -> None:
    # handler signs whatever nonce we stash; swap the email via a wrapped handler
    # by pointing the box at a domain no org claims
    r = await sso_client.get("/api/v1/auth/oidc/login", follow_redirects=False)
    q = parse_qs(urlparse(r.headers["location"]).query)
    _nonce_box["nonce"] = q["nonce"][0]
    _nonce_box["email_override"] = "stranger@evil.com"
    try:
        r2 = await sso_client.get(
            f"/api/v1/auth/oidc/callback?code=abc&state={q['state'][0]}",
            follow_redirects=False,
        )
        _assert_sso_error_redirect(r2, test_settings)
    finally:
        _nonce_box.pop("email_override", None)


async def test_bad_signature_rejected(
    sso_client: httpx.AsyncClient, test_settings: Settings
) -> None:
    """ID token signed by a key the published JWKS never advertises must be
    rejected -- otherwise anyone able to reach the token endpoint (or a
    replayed/forged response) could mint tokens for any identity."""
    r = await sso_client.get("/api/v1/auth/oidc/login", follow_redirects=False)
    q = parse_qs(urlparse(r.headers["location"]).query)
    _nonce_box["nonce"] = q["nonce"][0]
    _nonce_box["key_override"] = OTHER_KEY
    try:
        r2 = await sso_client.get(
            f"/api/v1/auth/oidc/callback?code=abc&state={q['state'][0]}",
            follow_redirects=False,
        )
        _assert_sso_error_redirect(r2, test_settings)
    finally:
        _nonce_box.pop("key_override", None)


async def test_wrong_audience_rejected(
    sso_client: httpx.AsyncClient, test_settings: Settings
) -> None:
    """An ID token issued for a different client_id (aud) must be rejected --
    otherwise a token minted for another relying party on the same IdP could
    be replayed here."""
    r = await sso_client.get("/api/v1/auth/oidc/login", follow_redirects=False)
    q = parse_qs(urlparse(r.headers["location"]).query)
    _nonce_box["nonce"] = q["nonce"][0]
    _nonce_box["aud_override"] = "some-other-client"
    try:
        r2 = await sso_client.get(
            f"/api/v1/auth/oidc/callback?code=abc&state={q['state'][0]}",
            follow_redirects=False,
        )
        _assert_sso_error_redirect(r2, test_settings)
    finally:
        _nonce_box.pop("aud_override", None)


async def test_wrong_issuer_rejected(
    sso_client: httpx.AsyncClient, test_settings: Settings
) -> None:
    """An ID token claiming a different issuer than the one discovered and
    configured must be rejected -- otherwise a compromised/lookalike IdP
    response could impersonate the trusted issuer."""
    r = await sso_client.get("/api/v1/auth/oidc/login", follow_redirects=False)
    q = parse_qs(urlparse(r.headers["location"]).query)
    _nonce_box["nonce"] = q["nonce"][0]
    _nonce_box["iss_override"] = "https://evil-idp.example.com"
    try:
        r2 = await sso_client.get(
            f"/api/v1/auth/oidc/callback?code=abc&state={q['state'][0]}",
            follow_redirects=False,
        )
        _assert_sso_error_redirect(r2, test_settings)
    finally:
        _nonce_box.pop("iss_override", None)


async def test_nonce_mismatch_rejected(
    sso_client: httpx.AsyncClient, test_settings: Settings
) -> None:
    """The ID token's nonce must match the one this app stashed in Redis for
    the state at login time -- otherwise a token obtained for an unrelated
    login attempt could be replayed into this callback (CSRF-ish token
    replay). Here we deliberately do NOT mirror the real login nonce into the
    box, so the IdP mock bakes a different one into the token than what's
    sitting in Redis."""
    r = await sso_client.get("/api/v1/auth/oidc/login", follow_redirects=False)
    q = parse_qs(urlparse(r.headers["location"]).query)
    real_nonce = q["nonce"][0]
    _nonce_box["nonce"] = "totally-different-nonce-" + real_nonce
    r2 = await sso_client.get(
        f"/api/v1/auth/oidc/callback?code=abc&state={q['state'][0]}",
        follow_redirects=False,
    )
    _assert_sso_error_redirect(r2, test_settings)
