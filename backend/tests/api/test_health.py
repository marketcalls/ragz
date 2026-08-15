"""RAGZ-PUB-03: /readyz must not let anonymous traffic exhaust the DB pool.
/readyz is unauthenticated by design (orchestrators probe it with no
credentials), so a burst of anonymous requests must collapse onto at most
one DB probe per `_READYZ_CACHE_TTL_SECONDS` window rather than checking
out a pooled connection per request. /healthz stays a pure in-memory
liveness check -- no DB dependency at all."""

from collections.abc import AsyncIterator

import httpx
import pytest
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine

from ragz.api.app import create_app
from ragz.api.routes import health as health_route
from ragz.core.config import Settings, get_settings
from ragz.core.db import build_session_factory
from tests.conftest import _stub_litellm_handler


@pytest.fixture(autouse=True)
def _reset_readyz_cache() -> AsyncIterator[None]:
    """The readiness cache is a module-level global (by design -- a simple
    timestamp+result cache, no new dependency) so it persists across tests
    in the same process unless reset."""
    health_route._readyz_cache = None
    yield
    health_route._readyz_cache = None


@pytest.fixture
async def counted_client(
    engine: AsyncEngine, redis_client: Redis, test_settings: Settings
) -> AsyncIterator[tuple[httpx.AsyncClient, list[int]]]:
    """Same wiring as tests/conftest.py's `client` fixture, except
    app.state.session_factory is wrapped to count how many times a DB
    session was actually opened -- the spy the caching behavior is proven
    against."""
    app = create_app(
        session_factory=build_session_factory(engine),
        redis_client=redis_client,
        litellm_transport=httpx.MockTransport(_stub_litellm_handler),
    )
    app.dependency_overrides[get_settings] = lambda: test_settings
    real_factory = app.state.session_factory
    calls: list[int] = []

    def counting_factory() -> object:
        calls.append(1)
        return real_factory()

    app.state.session_factory = counting_factory
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c, calls


async def test_healthz_touches_no_db(counted_client: tuple[httpx.AsyncClient, list[int]]) -> None:
    client, calls = counted_client
    r = await client.get("/healthz")
    assert r.status_code == 200 and r.json() == {"status": "ok"}
    assert calls == []


async def test_readyz_returns_ready(counted_client: tuple[httpx.AsyncClient, list[int]]) -> None:
    client, calls = counted_client
    r = await client.get("/readyz")
    assert r.status_code == 200 and r.json() == {"status": "ready"}
    assert len(calls) == 1


async def test_readyz_burst_issues_at_most_one_db_probe(
    counted_client: tuple[httpx.AsyncClient, list[int]],
) -> None:
    """A flood of N anonymous /readyz requests inside the TTL window must
    collapse onto a single DB probe, not one pooled connection per request."""
    client, calls = counted_client
    for _ in range(20):
        r = await client.get("/readyz")
        assert r.status_code == 200 and r.json() == {"status": "ready"}
    assert len(calls) == 1


async def test_readyz_reports_not_ready_when_db_probe_fails(
    counted_client: tuple[httpx.AsyncClient, list[int]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _calls = counted_client

    async def _boom(request: object) -> bool:
        return False

    monkeypatch.setattr(health_route, "_probe_db", _boom)
    r = await client.get("/readyz")
    assert r.status_code == 503 and r.json() == {"status": "unavailable"}


async def test_readyz_reprobes_after_ttl_expires(
    counted_client: tuple[httpx.AsyncClient, list[int]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Once the TTL window elapses, the next request re-probes rather than
    serving a stale cached result forever."""
    client, calls = counted_client
    monkeypatch.setattr(health_route, "_READYZ_CACHE_TTL_SECONDS", 0.0)
    r1 = await client.get("/readyz")
    r2 = await client.get("/readyz")
    assert r1.status_code == 200 and r2.status_code == 200
    assert len(calls) == 2
