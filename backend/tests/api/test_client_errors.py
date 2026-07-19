import httpx

from raghub.modules.auth.models import User


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
