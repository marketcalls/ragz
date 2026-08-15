import asyncio
from typing import Any

import httpx
import pytest
from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse
from redis.asyncio import Redis

from ragz.core import ratelimit as ratelimit_module
from ragz.core.config import Settings
from ragz.core.errors import RagzError, RateLimitExceeded
from ragz.core.ratelimit import check_rate_limit, rate_limit
from ragz.modules.auth.models import User


async def test_check_rate_limit_blocks_then_window_resets(redis_client: Redis) -> None:
    for _ in range(2):
        await check_rate_limit(redis_client, "rl:t:1", limit=2, window_seconds=1)
    with pytest.raises(RateLimitExceeded):
        await check_rate_limit(redis_client, "rl:t:1", limit=2, window_seconds=1)
    await asyncio.sleep(1.1)  # fixed window expires via EXPIRE
    await check_rate_limit(redis_client, "rl:t:1", limit=2, window_seconds=1)


async def test_login_rate_limited(client: httpx.AsyncClient, seeded_user: User) -> None:
    for _ in range(10):
        await client.post("/api/v1/auth/login", json={"email": "a@acme.com", "password": "bad"})
    r = await client.post("/api/v1/auth/login",
                          json={"email": "a@acme.com", "password": "pw123456"})
    assert r.status_code == 429
    assert r.headers["content-type"].startswith("application/problem+json")


async def test_refresh_rate_limited(client: httpx.AsyncClient, seeded_user: User) -> None:
    await client.post("/api/v1/auth/login",
                      json={"email": "a@acme.com", "password": "pw123456"})
    for _ in range(30):
        await client.post("/api/v1/auth/refresh")  # guard runs regardless of outcome
    assert (await client.post("/api/v1/auth/refresh")).status_code == 429


async def test_invitation_accept_rate_limited(client: httpx.AsyncClient) -> None:
    for _ in range(10):
        await client.post("/api/v1/auth/invitations/accept",
                          json={"token": "bogus", "password": "irrelevant1"})
    r = await client.post("/api/v1/auth/invitations/accept",
                          json={"token": "bogus", "password": "irrelevant1"})
    assert r.status_code == 429


async def test_oidc_login_rate_limited(client: httpx.AsyncClient) -> None:
    # SSO isn't configured in this fixture, so the route itself 404s -- but the
    # rate-limit dependency runs before the handler body regardless of outcome
    # (same guard-runs-regardless-of-outcome pattern as /auth/refresh above).
    for _ in range(10):
        await client.get("/api/v1/auth/oidc/login", follow_redirects=False)
    r = await client.get("/api/v1/auth/oidc/login", follow_redirects=False)
    assert r.status_code == 429
    assert r.headers["content-type"].startswith("application/problem+json")


async def test_oidc_callback_rate_limited_redirects_not_problem_json(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Unlike every other rate-limited route, /oidc/callback is a top-level
    # browser navigation (redirect from the IdP) -- a 429 problem+json body
    # would render as raw JSON to the user instead of sending them back to
    # login. Contract C7 says this route must redirect on every failure mode,
    # rate-limiting included.
    #
    # SSO isn't configured in this fixture, so *every* call -- the 10
    # warm-up calls and the 11th alike -- already redirects via NotFoundError
    # on the same except tuple. That makes the 302 assertion below
    # indistinguishable from the generic "SSO not configured" path, so it
    # can't prove rate limiting actually fired. To give the test real
    # discriminating power, spy on the real (redis-backed) rate limiter --
    # delegating to the genuine implementation, not faking it -- and assert
    # it ran on every request (if `await rate_limit(...)(request)` were ever
    # deleted from the route, this spy would never be called at all) and
    # that only the 11th invocation is the one that actually raised
    # RateLimitExceeded.
    real_check_rate_limit = ratelimit_module.check_rate_limit
    raised_on_call: list[bool] = []

    async def spy_check_rate_limit(*args: Any, **kwargs: Any) -> None:
        try:
            await real_check_rate_limit(*args, **kwargs)
        except RateLimitExceeded:
            raised_on_call.append(True)
            raise
        else:
            raised_on_call.append(False)

    monkeypatch.setattr(ratelimit_module, "check_rate_limit", spy_check_rate_limit)

    for _ in range(10):
        await client.get("/api/v1/auth/oidc/callback?code=x&state=y", follow_redirects=False)
    r = await client.get("/api/v1/auth/oidc/callback?code=x&state=y", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"].endswith("/login?sso_error=1")
    # The discriminating assertion: the real limiter ran on every request,
    # and the 11th run -- not the 10 before it -- is what exceeded the limit.
    assert raised_on_call == [False] * 10 + [True]


async def test_oidc_status_rate_limited(client: httpx.AsyncClient) -> None:
    # Looser tier than login/callback, but still bounded.
    for _ in range(60):
        await client.get("/api/v1/auth/oidc/status")
    r = await client.get("/api/v1/auth/oidc/status")
    assert r.status_code == 429


def _build_probe_app(redis_client: Redis, scope: str, limit: int = 2) -> FastAPI:
    """Minimal standalone app carrying just `rate_limit()` on one route --
    the real `client` fixture's ASGITransport hardcodes a single fixed peer
    for its whole lifetime (httpx.ASGITransport's `client` tuple), so these
    tests build their own app + transport per peer address instead."""
    app = FastAPI()
    app.state.redis = redis_client

    @app.exception_handler(RagzError)
    async def _handle_ragz_error(request: Any, exc: RagzError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"detail": str(exc)})

    @app.get("/probe", dependencies=[Depends(rate_limit(scope, limit=limit, window_seconds=60))])
    async def probe() -> dict[str, bool]:
        return {"ok": True}

    return app


async def test_rate_limit_keys_on_resolved_client_ip_behind_trusted_proxy(
    redis_client: Redis, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RAGZ-PUB-06 follow-up: behind a configured trusted reverse proxy, two
    different real clients -- distinguished only by X-Forwarded-For, sharing
    the same TCP peer (the proxy) -- get INDEPENDENT rate-limit buckets."""
    trusted_peer = "10.10.10.10"
    settings = Settings(_env_file=None, trusted_proxies=[f"{trusted_peer}/32"])
    monkeypatch.setattr(ratelimit_module, "get_settings", lambda: settings)

    app = _build_probe_app(redis_client, scope="probe_trusted")
    transport = httpx.ASGITransport(app=app, client=(trusted_peer, 12345))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as probe_client:
        for _ in range(2):
            assert (
                await probe_client.get("/probe", headers={"X-Forwarded-For": "1.1.1.1"})
            ).status_code == 200
        assert (
            await probe_client.get("/probe", headers={"X-Forwarded-For": "1.1.1.1"})
        ).status_code == 429

        # A different real client behind the SAME proxy peer: untouched budget.
        for _ in range(2):
            assert (
                await probe_client.get("/probe", headers={"X-Forwarded-For": "2.2.2.2"})
            ).status_code == 200
        assert (
            await probe_client.get("/probe", headers={"X-Forwarded-For": "2.2.2.2"})
        ).status_code == 429


async def test_rate_limit_ignores_spoofed_xff_from_untrusted_peer(
    redis_client: Redis, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The spoof-resistance guarantee at the rate-limit call site: a direct
    (non-proxy) caller varying X-Forwarded-For per request must NOT escape
    the limit by "becoming" a different client each time -- every request
    shares one bucket keyed on the real (untrusted) peer."""
    settings = Settings(_env_file=None, trusted_proxies=["10.10.10.10/32"])
    monkeypatch.setattr(ratelimit_module, "get_settings", lambda: settings)

    app = _build_probe_app(redis_client, scope="probe_untrusted")
    untrusted_peer = "203.0.113.5"
    transport = httpx.ASGITransport(app=app, client=(untrusted_peer, 12345))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as probe_client:
        assert (
            await probe_client.get("/probe", headers={"X-Forwarded-For": "9.9.9.9"})
        ).status_code == 200
        assert (
            await probe_client.get("/probe", headers={"X-Forwarded-For": "8.8.8.8"})
        ).status_code == 200
        # Third request, yet another spoofed identity -- still the same
        # bucket (keyed on the real peer), so the limit trips here.
        assert (
            await probe_client.get("/probe", headers={"X-Forwarded-For": "7.7.7.7"})
        ).status_code == 429
