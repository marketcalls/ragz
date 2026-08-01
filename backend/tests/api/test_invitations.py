import httpx

from ragz.modules.auth.models import User


async def auth(client: httpx.AsyncClient, email: str) -> dict[str, str]:
    r = await client.post("/api/v1/auth/login", json={"email": email, "password": "pw123456"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def test_invite_flow(client: httpx.AsyncClient, seeded_user: User) -> None:
    h = await auth(client, "a@acme.com")
    r = await client.post(
        "/api/v1/auth/invitations", json={"email": "new@acme.com", "role": "user"}, headers=h
    )
    assert r.status_code == 201
    token = r.json()["invite_token"]

    r2 = await client.post(
        "/api/v1/auth/invitations/accept", json={"token": token, "password": "newpw1234567"}
    )
    assert r2.status_code == 201

    r3 = await client.post(
        "/api/v1/auth/login", json={"email": "new@acme.com", "password": "newpw1234567"}
    )
    assert r3.status_code == 200
    # token is single-use
    r4 = await client.post(
        "/api/v1/auth/invitations/accept", json={"token": token, "password": "other1234567"}
    )
    assert r4.status_code == 401


async def test_invite_requires_admin(client: httpx.AsyncClient, seeded_user: User) -> None:
    r = await client.post("/api/v1/auth/invitations", json={"email": "x@x.com", "role": "user"})
    assert r.status_code == 401


async def test_invite_rejects_invalid_role(client: httpx.AsyncClient, seeded_user: User) -> None:
    h = await auth(client, "a@acme.com")
    r = await client.post(
        "/api/v1/auth/invitations", json={"email": "bad@acme.com", "role": "bogus"}, headers=h
    )
    assert r.status_code == 422


async def test_invite_accept_rejects_short_password(
    client: httpx.AsyncClient, seeded_user: User
) -> None:
    h = await auth(client, "a@acme.com")
    r = await client.post(
        "/api/v1/auth/invitations", json={"email": "short@acme.com", "role": "user"}, headers=h
    )
    assert r.status_code == 201
    token = r.json()["invite_token"]

    r2 = await client.post(
        "/api/v1/auth/invitations/accept", json={"token": token, "password": "short123"}
    )
    assert r2.status_code == 422
