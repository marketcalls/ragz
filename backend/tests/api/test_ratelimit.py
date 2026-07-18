import httpx

from raghub.modules.auth.models import User


async def test_login_rate_limited(client: httpx.AsyncClient, seeded_user: User) -> None:
    for _ in range(10):
        await client.post("/api/v1/auth/login", json={"email": "a@acme.com", "password": "bad"})
    r = await client.post(
        "/api/v1/auth/login", json={"email": "a@acme.com", "password": "pw123456"}
    )
    assert r.status_code == 429
    assert r.headers["content-type"].startswith("application/problem+json")
