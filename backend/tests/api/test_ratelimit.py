import asyncio

import httpx
import pytest
from redis.asyncio import Redis

from raghub.core.errors import RateLimitExceeded
from raghub.core.ratelimit import check_rate_limit
from raghub.modules.auth.models import User


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
    client: httpx.AsyncClient,
) -> None:
    # Unlike every other rate-limited route, /oidc/callback is a top-level
    # browser navigation (redirect from the IdP) -- a 429 problem+json body
    # would render as raw JSON to the user instead of sending them back to
    # login. Contract C7 says this route must redirect on every failure mode,
    # rate-limiting included.
    for _ in range(10):
        await client.get("/api/v1/auth/oidc/callback?code=x&state=y", follow_redirects=False)
    r = await client.get("/api/v1/auth/oidc/callback?code=x&state=y", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"].endswith("/login?sso_error=1")


async def test_oidc_status_rate_limited(client: httpx.AsyncClient) -> None:
    # Looser tier than login/callback, but still bounded.
    for _ in range(60):
        await client.get("/api/v1/auth/oidc/status")
    r = await client.get("/api/v1/auth/oidc/status")
    assert r.status_code == 429
