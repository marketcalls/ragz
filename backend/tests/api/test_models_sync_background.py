from collections.abc import AsyncIterator

import httpx
import pytest
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine

from raghub.api.app import create_app
from raghub.core.config import Settings, get_settings
from raghub.core.db import build_session_factory
from raghub.modules.auth.models import User

OPENAI_BODY = {
    "litellm_model_name": "gpt-4o-mini", "display_name": "GPT-4o mini",
    "provider_kind": "openai", "api_key": "sk-live-abc",
}


async def auth(client: httpx.AsyncClient, email: str) -> dict[str, str]:
    r = await client.post("/api/v1/auth/login", json={"email": email, "password": "pw123456"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _failing_litellm_handler(request: httpx.Request) -> httpx.Response:
    """Stub a proxy that accepts the model list but rejects every replay
    write, so sync_models_to_litellm's httpx.HTTPStatusError path fires and
    the whole replay is persisted as sync_status='error'."""
    if request.url.path == "/v1/model/info":
        return httpx.Response(200, json={"data": []})
    if request.url.path == "/model/new":
        return httpx.Response(500, json={"error": "boom"})
    return httpx.Response(200, json={})


@pytest.fixture
async def failing_sync_client(
    engine: AsyncEngine, redis_client: Redis, test_settings: Settings
) -> AsyncIterator[httpx.AsyncClient]:
    """Same wiring as the `client` fixture in tests/conftest.py, except the
    LiteLLM transport fails every replay write - used to exercise the
    `except UpstreamError: pass` background-task path in
    api/routes/models.py::_background_sync at the route level."""
    app = create_app(
        session_factory=build_session_factory(engine),
        redis_client=redis_client,
        litellm_transport=httpx.MockTransport(_failing_litellm_handler),
    )
    app.dependency_overrides[get_settings] = lambda: test_settings
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_model_create_sync_failure_still_returns_201(
    failing_sync_client: httpx.AsyncClient, seeded_superadmin: User
) -> None:
    """The background replay failing must not surface as a 5xx on the
    request that scheduled it: POST still returns 201 with the row's
    pre-sync status, and the failure is only observable on a later GET as
    sync_status='error' (persisted by sync_models_to_litellm itself)."""
    h = await auth(failing_sync_client, "root@platform.example")
    r = await failing_sync_client.post("/api/v1/admin/models", json=OPENAI_BODY, headers=h)
    assert r.status_code == 201
    assert r.json()["sync_status"] == "pending"

    listing = await failing_sync_client.get("/api/v1/admin/models", headers=h)
    assert listing.json()[0]["sync_status"] == "error"


async def test_model_create_returns_before_sync_completes(
    client: httpx.AsyncClient, seeded_superadmin: User
) -> None:
    """POST /api/v1/admin/models responds 201; the replay to LiteLLM happens as a
    background task (ASGITransport runs background tasks after the response), and
    the row ends in sync_status='synced'."""
    h = await auth(client, "root@platform.example")
    r = await client.post("/api/v1/admin/models", json=OPENAI_BODY, headers=h)
    assert r.status_code == 201
    # The response was serialized before the background replay ran, so it still
    # carries the row's pre-sync default rather than the post-replay outcome.
    assert r.json()["sync_status"] == "pending"

    listing = await client.get("/api/v1/admin/models", headers=h)
    assert listing.json()[0]["sync_status"] == "synced"


async def test_model_create_survives_non_upstream_sync_failure(
    client: httpx.AsyncClient, seeded_superadmin: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_background_sync only special-cased UpstreamError; any other exception
    (e.g. a programming error, a DB hiccup on the fresh session) used to
    escape the background task and leave the row 'pending' forever with no
    way for the polling admin UI to learn the sync ended. The broad except
    tail must swallow it (and only log) so the request that scheduled the
    sync is unaffected."""

    async def _boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr("raghub.api.routes.models.sync_models_to_litellm", _boom)

    h = await auth(client, "root@platform.example")
    r = await client.post("/api/v1/admin/models", json=OPENAI_BODY, headers=h)
    assert r.status_code == 201
    assert r.json()["sync_status"] == "pending"

    # No unhandled exception escaped the background task; the row is simply
    # left at its pre-sync status since sync_models_to_litellm itself never
    # ran far enough to persist anything.
    listing = await client.get("/api/v1/admin/models", headers=h)
    assert listing.json()[0]["sync_status"] == "pending"


async def test_model_delete_triggers_background_sync(
    client: httpx.AsyncClient, seeded_superadmin: User
) -> None:
    h = await auth(client, "root@platform.example")
    created = await client.post("/api/v1/admin/models", json=OPENAI_BODY, headers=h)
    model_id = created.json()["id"]

    other = await client.post(
        "/api/v1/admin/models",
        json={"litellm_model_name": "llama3", "display_name": "Llama",
              "provider_kind": "ollama", "base_url": "http://ollama:11434"},
        headers=h,
    )
    other_id = other.json()["id"]

    r = await client.delete(f"/api/v1/admin/models/{model_id}", headers=h)
    assert r.status_code == 204

    listing = (await client.get("/api/v1/admin/models", headers=h)).json()
    assert [m["id"] for m in listing] == [other_id]
    assert listing[0]["sync_status"] == "synced"
