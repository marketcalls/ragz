from collections.abc import AsyncIterator

import httpx
import pytest
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from raghub.api.app import create_app
from raghub.core.db import build_session_factory
from raghub.modules.auth.models import User


@pytest.fixture
async def crashy_client(
    engine: AsyncEngine, redis_client: Redis
) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(session_factory=build_session_factory(engine), redis_client=redis_client)

    @app.get("/probe/boom")
    async def boom() -> None:
        raise RuntimeError("kaboom internal secret detail")

    # raise_app_exceptions=False: Starlette's ServerErrorMiddleware re-raises after
    # sending the handler's response; without this flag the transport surfaces the
    # exception instead of the 500 body.
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_catch_all_returns_generic_problem_json(crashy_client: httpx.AsyncClient) -> None:
    r = await crashy_client.get("/probe/boom")
    assert r.status_code == 500
    assert r.headers["content-type"].startswith("application/problem+json")
    body = r.json()
    assert body["title"] == "Internal error"
    assert "kaboom" not in r.text  # internals never leak


async def test_integrity_error_maps_to_409(
    client: httpx.AsyncClient, seeded_user: User, session: AsyncSession
) -> None:
    plain = User(org_id=seeded_user.org_id, email="p@acme.com",
                 password_hash=seeded_user.password_hash, role="user")
    session.add(plain)
    await session.commit()
    r = await client.post("/api/v1/auth/login",
                          json={"email": "a@acme.com", "password": "pw123456"})
    h = {"Authorization": f"Bearer {r.json()['access_token']}"}
    ws = await client.post("/api/v1/workspaces", json={"name": "F"}, headers=h)
    ws_id = ws.json()["id"]
    body = {"user_id": str(plain.id)}
    assert (await client.post(f"/api/v1/workspaces/{ws_id}/members", json=body,
                              headers=h)).status_code == 204
    # second insert violates the (workspace_id, user_id) primary key
    r2 = await client.post(f"/api/v1/workspaces/{ws_id}/members", json=body, headers=h)
    assert r2.status_code == 409
    assert r2.headers["content-type"].startswith("application/problem+json")
