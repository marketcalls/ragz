import httpx
from redis.asyncio import Redis

from ragz.api.routes.client_errors import _KEY
from ragz.modules.auth.models import User


async def test_post_and_superadmin_read(
    client: httpx.AsyncClient, seeded_user: User, superadmin_headers: dict[str, str],
    user_headers: dict[str, str],
) -> None:
    r = await client.post("/api/v1/client-errors", headers=user_headers,
                          json={"message": "boom", "stack": "at x", "url": "/chat"})
    assert r.status_code == 204
    r = await client.get("/api/v1/superadmin/client-errors", headers=user_headers)
    assert r.status_code == 403
    r = await client.get("/api/v1/superadmin/client-errors", headers=superadmin_headers)
    assert r.status_code == 200
    entry = r.json()[0]
    assert entry["message"] == "boom" and "user_id" in entry and "ts" in entry


async def test_unauthenticated_rejected(client: httpx.AsyncClient) -> None:
    r = await client.post("/api/v1/client-errors", json={"message": "x"})
    assert r.status_code == 401


async def test_malformed_entry_is_skipped(
    client: httpx.AsyncClient,
    redis_client: Redis,
    seeded_user: User,
    superadmin_headers: dict[str, str],
    user_headers: dict[str, str],
) -> None:
    """A stale/malformed ring-buffer entry must not 500 the superadmin read --
    it should simply be skipped, leaving the valid entries intact."""
    r = await client.post("/api/v1/client-errors", headers=user_headers,
                          json={"message": "still good", "url": "/chat"})
    assert r.status_code == 204

    await redis_client.lpush(_KEY, "not json at all {{{")

    r = await client.get("/api/v1/superadmin/client-errors", headers=superadmin_headers)
    assert r.status_code == 200
    entries = r.json()
    assert len(entries) == 1
    assert entries[0]["message"] == "still good"


async def test_limit_zero_rejected(
    client: httpx.AsyncClient, superadmin_headers: dict[str, str],
) -> None:
    r = await client.get(
        "/api/v1/superadmin/client-errors", headers=superadmin_headers, params={"limit": 0}
    )
    assert r.status_code == 422


async def test_limit_negative_rejected(
    client: httpx.AsyncClient, superadmin_headers: dict[str, str],
) -> None:
    r = await client.get(
        "/api/v1/superadmin/client-errors", headers=superadmin_headers, params={"limit": -1}
    )
    assert r.status_code == 422
