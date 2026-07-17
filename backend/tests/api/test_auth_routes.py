import httpx

from raghub.modules.auth.models import User


async def test_login_ok(client: httpx.AsyncClient, seeded_user: User) -> None:
    r = await client.post(
        "/api/v1/auth/login", json={"email": "a@acme.com", "password": "pw123456"}
    )
    assert r.status_code == 200
    assert r.json()["access_token"]
    assert "refresh_token" in r.cookies


async def test_login_bad_password_problem_json(
    client: httpx.AsyncClient, seeded_user: User
) -> None:
    r = await client.post("/api/v1/auth/login", json={"email": "a@acme.com", "password": "bad"})
    assert r.status_code == 401
    assert r.headers["content-type"].startswith("application/problem+json")
    assert r.json()["title"] == "Authentication failed"


async def test_refresh_and_logout(client: httpx.AsyncClient, seeded_user: User) -> None:
    r = await client.post(
        "/api/v1/auth/login", json={"email": "a@acme.com", "password": "pw123456"}
    )
    r2 = await client.post("/api/v1/auth/refresh")
    assert r2.status_code == 200
    assert r2.cookies["refresh_token"] != r.cookies["refresh_token"]
    r3 = await client.post("/api/v1/auth/logout")
    assert r3.status_code == 204
    assert (await client.post("/api/v1/auth/refresh")).status_code == 401
