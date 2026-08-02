import httpx

from ragz.modules.auth.models import User


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


async def test_refresh_concurrent_tabs_grace_reissue(
    client: httpx.AsyncClient, seeded_user: User
) -> None:
    """Two tabs share one refresh cookie; the tab that loses the rotation race
    replays the just-rotated token within the grace window and must get a fresh
    pair (200) instead of tripping family revocation."""
    r = await client.post(
        "/api/v1/auth/login", json={"email": "a@acme.com", "password": "pw123456"}
    )
    original = r.cookies["refresh_token"]
    r2 = await client.post("/api/v1/auth/refresh")  # tab A wins the race
    assert r2.status_code == 200
    # tab B still holds the pre-rotation cookie
    client.cookies.set("refresh_token", original, path="/api/v1/auth")
    r3 = await client.post("/api/v1/auth/refresh")
    assert r3.status_code == 200
    assert r3.json()["access_token"]
    assert r3.cookies["refresh_token"] not in {original, r2.cookies["refresh_token"]}
    # tab A's cookie survived the race (family not revoked)
    client.cookies.set("refresh_token", r2.cookies["refresh_token"], path="/api/v1/auth")
    assert (await client.post("/api/v1/auth/refresh")).status_code == 200
