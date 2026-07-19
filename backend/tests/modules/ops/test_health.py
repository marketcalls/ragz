"""Superadmin health endpoint (SUP-4): each component degrades independently
and never turns into a 500, queue depth reads real Redis list lengths, and
the route is gated to superadmin (mirrors F's platform_usage_by_org gating
test in tests/api/test_usage_endpoints.py).
"""

from collections.abc import AsyncIterator

import httpx
import pytest
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine

from raghub.api.app import create_app
from raghub.core.config import Settings, get_settings
from raghub.core.db import build_session_factory
from raghub.modules.auth.models import User


async def auth(client: httpx.AsyncClient, email: str) -> dict[str, str]:
    r = await client.post("/api/v1/auth/login", json={"email": email, "password": "pw123456"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _degraded_qdrant_handler(request: httpx.Request) -> httpx.Response:
    """Qdrant's /collections 500s; LiteLLM's liveliness probe still answers ok."""
    if request.url.path.startswith("/collections"):
        return httpx.Response(500, json={"error": "boom"})
    if request.url.path == "/health/liveliness":
        return httpx.Response(200, json={})
    return httpx.Response(200, json={})


@pytest.fixture
async def degraded_qdrant_client(
    engine: AsyncEngine, redis_client: Redis, test_settings: Settings
) -> AsyncIterator[httpx.AsyncClient]:
    """Same wiring as tests/conftest.py's `client` fixture, except the shared
    httpx transport 500s every Qdrant call - exercises the per-component
    degrade path at the route level."""
    app = create_app(
        session_factory=build_session_factory(engine),
        redis_client=redis_client,
        litellm_transport=httpx.MockTransport(_degraded_qdrant_handler),
    )
    app.dependency_overrides[get_settings] = lambda: test_settings
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_health_degrades_per_component(
    degraded_qdrant_client: httpx.AsyncClient, seeded_superadmin: User
) -> None:
    h = await auth(degraded_qdrant_client, "root@platform.example")
    r = await degraded_qdrant_client.get("/api/v1/superadmin/health", headers=h)
    assert r.status_code == 200
    body = r.json()
    assert body["qdrant"]["status"] == "error"
    assert body["litellm"]["status"] == "ok"


async def test_queue_depths_from_redis(
    client: httpx.AsyncClient, seeded_superadmin: User, redis_client: Redis
) -> None:
    await redis_client.lpush("default", "task1", "task2", "task3")
    h = await auth(client, "root@platform.example")
    r = await client.get("/api/v1/superadmin/health", headers=h)
    assert r.status_code == 200
    body = r.json()
    assert body["queues"]["status"] == "ok"
    assert body["queues"]["depths"] == {"default": 3, "interactive": 0}


async def test_requires_superadmin(client: httpx.AsyncClient, seeded_user: User) -> None:
    h = await auth(client, "a@acme.com")
    r = await client.get("/api/v1/superadmin/health", headers=h)
    assert r.status_code == 403
