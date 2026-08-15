"""Superadmin health endpoint (SUP-4): each component degrades independently
and never turns into a 500, queue depth reads real Redis list lengths, and
the route is gated to superadmin (mirrors F's platform_usage_by_org gating
test in tests/api/test_usage_endpoints.py).

Review round 1 additions: the org rollup (platform_usage_by_org) and the
Redis LLEN probe must degrade-not-fail too - a DB error must not 500 the
whole endpoint, and a blackholed Redis must not hang the gather forever.
"""

import asyncio
from collections.abc import AsyncIterator

import httpx
import pytest
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from ragz.api.app import create_app
from ragz.api.routes import superadmin_ops
from ragz.core.config import Settings, get_settings
from ragz.core.db import build_session_factory
from ragz.modules.auth.models import User
from ragz.modules.ops import health as ops_health


class _FakeStorage:
    """Stand-in for ObjectStorage (backend/src/ragz/core/storage.py) --
    avoids a real MinIO dependency in these tests, mirroring the injectable
    httpx-transport pattern already used for qdrant/litellm/embedder/reranker
    above. `fail=True` makes `head_bucket` raise, simulating MinIO down."""

    def __init__(self, *, fail: bool = False) -> None:
        self._fail = fail

    async def head_bucket(self) -> None:
        if self._fail:
            raise ConnectionError("minio unreachable")


@pytest.fixture(autouse=True)
def _stub_minio_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every test below exercises the full /superadmin/health route, which
    now includes a MinIO probe. Default it to a fast, deterministic success
    so these tests don't depend on a real MinIO instance being reachable at
    the configured endpoint; tests that want the degrade path re-monkeypatch
    `build_storage` themselves (monkeypatch allows repeated calls)."""
    monkeypatch.setattr(ops_health, "build_storage", lambda settings: _FakeStorage())


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


async def test_org_rollup_failure_degrades(
    client: httpx.AsyncClient,
    seeded_superadmin: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A DB failure inside platform_usage_by_org must not 500 the endpoint -
    it degrades the orgs component while the other probes stay intact."""

    async def _boom(session: object, *, days: int = 30) -> list[dict[str, object]]:
        raise RuntimeError("db unavailable")

    monkeypatch.setattr(superadmin_ops, "platform_usage_by_org", _boom)
    h = await auth(client, "root@platform.example")
    r = await client.get("/api/v1/superadmin/health", headers=h)
    assert r.status_code == 200
    body = r.json()
    assert body["orgs"] == {"status": "error", "detail": "RuntimeError"}
    # Other probes are independent of the org rollup and stay intact - the
    # `client` fixture doesn't point qdrant at a live instance (that's what
    # `degraded_qdrant_client` is for above), so only assert the two probes
    # this fixture actually exercises successfully.
    assert body["queues"]["status"] == "ok"
    assert body["litellm"]["status"] == "ok"


async def test_queue_depth_timeout_degrades(
    client: httpx.AsyncClient,
    seeded_superadmin: User,
    redis_client: Redis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A blackholed Redis LLEN must time out and degrade the queues component
    instead of hanging the whole gather forever. The module-level timeout is
    monkeypatched down so the test stays fast."""
    monkeypatch.setattr(ops_health, "QUEUE_TIMEOUT_SECONDS", 0.05)

    async def _slow_llen(_key: str) -> int:
        await asyncio.sleep(1.0)
        return 0

    monkeypatch.setattr(redis_client, "llen", _slow_llen)
    h = await auth(client, "root@platform.example")
    r = await client.get("/api/v1/superadmin/health", headers=h)
    assert r.status_code == 200
    body = r.json()
    assert body["queues"] == {"status": "error", "detail": "TimeoutError"}
    assert body["litellm"]["status"] == "ok"


# ---------------------------------------------------------------------------
# SUP-4 follow-up: every dependency probed (db/redis/minio/embedder/reranker),
# each with status + latency_ms, each bounded so a hung dependency degrades
# fast instead of stalling the whole endpoint. A down reranker and a
# multi-second-slow embedder were previously invisible -- these tests prove
# they now surface.
# ---------------------------------------------------------------------------


async def test_health_returns_every_dependency_key(
    client: httpx.AsyncClient, seeded_superadmin: User
) -> None:
    h = await auth(client, "root@platform.example")
    r = await client.get("/api/v1/superadmin/health", headers=h)
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {
        "db", "redis", "queues", "qdrant", "minio", "embedder", "reranker", "litellm", "orgs",
    }
    for key in ("db", "redis", "minio", "embedder", "reranker"):
        assert body[key]["status"] == "ok", f"{key}: {body[key]}"
        assert isinstance(body[key]["latency_ms"], int)
        assert body[key]["latency_ms"] >= 0


def _degraded_reranker_handler(request: httpx.Request) -> httpx.Response:
    """The reranker's /health is unreachable (connection refused); everything
    else (qdrant, litellm, embedder) answers ok. Distinguished by port since
    embedder (tei_url, :58080) and reranker (rerank_url, :58081) share the
    same MockTransport and the same "/health" path."""
    if request.url.port == 58081:
        raise httpx.ConnectError("connection refused", request=request)
    return httpx.Response(200, json={})


@pytest.fixture
async def degraded_reranker_client(
    engine: AsyncEngine, redis_client: Redis, test_settings: Settings
) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(
        session_factory=build_session_factory(engine),
        redis_client=redis_client,
        litellm_transport=httpx.MockTransport(_degraded_reranker_handler),
    )
    app.dependency_overrides[get_settings] = lambda: test_settings
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_reranker_down_degrades_independently_of_the_rest(
    degraded_reranker_client: httpx.AsyncClient, seeded_superadmin: User
) -> None:
    h = await auth(degraded_reranker_client, "root@platform.example")
    r = await degraded_reranker_client.get("/api/v1/superadmin/health", headers=h)
    assert r.status_code == 200
    body = r.json()
    assert body["reranker"]["status"] == "error"
    assert body["reranker"]["detail"] == "ConnectError"
    assert isinstance(body["reranker"]["latency_ms"], int)
    assert body["embedder"]["status"] == "ok"
    assert body["litellm"]["status"] == "ok"


async def test_db_health_ok(session: AsyncSession) -> None:
    result = await ops_health.db_health(session)
    assert result["status"] == "ok"
    assert isinstance(result["latency_ms"], int)


async def test_db_health_degrades_on_error(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("db unavailable")

    monkeypatch.setattr(session, "execute", _boom)
    result = await ops_health.db_health(session)
    assert result["status"] == "error"
    assert result["detail"] == "RuntimeError"
    assert isinstance(result["latency_ms"], int)


async def test_redis_health_ok(redis_client: Redis) -> None:
    result = await ops_health.redis_health(redis_client)
    assert result["status"] == "ok"
    assert isinstance(result["latency_ms"], int)


async def test_redis_health_timeout_degrades(
    redis_client: Redis, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ops_health, "PROBE_TIMEOUT_SECONDS", 0.05)

    async def _slow_ping() -> bool:
        await asyncio.sleep(1.0)
        return True

    monkeypatch.setattr(redis_client, "ping", _slow_ping)
    result = await ops_health.redis_health(redis_client)
    assert result["status"] == "error"
    assert result["detail"] == "TimeoutError"
    assert isinstance(result["latency_ms"], int)


def _health_ok_handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={})


def _connect_refused_handler(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("connection refused", request=request)


async def test_embedder_health_ok(test_settings: Settings) -> None:
    transport = httpx.MockTransport(_health_ok_handler)
    result = await ops_health.embedder_health(test_settings, transport)
    assert result["status"] == "ok"
    assert isinstance(result["latency_ms"], int)


async def test_embedder_health_degrades_when_down(test_settings: Settings) -> None:
    result = await ops_health.embedder_health(
        test_settings, httpx.MockTransport(_connect_refused_handler)
    )
    assert result["status"] == "error"
    assert result["detail"] == "ConnectError"
    assert isinstance(result["latency_ms"], int)


async def test_embedder_health_short_timeout_bounds_a_slow_dependency(
    test_settings: Settings,
) -> None:
    """A slow/hung embedder must surface as a fast error bounded by the
    probe's own short client timeout, not a multi-second hang -- this is the
    exact incident from the brief (a real 12s-slow embedder was invisible
    before this probe existed)."""

    async def _slow_handler(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(1.0)
        return httpx.Response(200, json={})

    result = await ops_health.embedder_health(
        test_settings, httpx.MockTransport(_slow_handler), timeout=0.05
    )
    assert result["status"] == "error"
    assert result["detail"] == "TimeoutError"
    assert result["latency_ms"] < 1000


async def test_reranker_health_ok(test_settings: Settings) -> None:
    transport = httpx.MockTransport(_health_ok_handler)
    result = await ops_health.reranker_health(test_settings, transport)
    assert result["status"] == "ok"
    assert isinstance(result["latency_ms"], int)


async def test_reranker_health_degrades_when_down(test_settings: Settings) -> None:
    result = await ops_health.reranker_health(
        test_settings, httpx.MockTransport(_connect_refused_handler)
    )
    assert result["status"] == "error"
    assert result["detail"] == "ConnectError"
    assert isinstance(result["latency_ms"], int)


async def test_minio_health_ok(test_settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ops_health, "build_storage", lambda settings: _FakeStorage())
    result = await ops_health.minio_health(test_settings)
    assert result["status"] == "ok"
    assert isinstance(result["latency_ms"], int)


async def test_minio_health_degrades_when_down(
    test_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ops_health, "build_storage", lambda settings: _FakeStorage(fail=True))
    result = await ops_health.minio_health(test_settings)
    assert result["status"] == "error"
    assert result["detail"] == "ConnectionError"
    assert isinstance(result["latency_ms"], int)
