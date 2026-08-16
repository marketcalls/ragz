import uuid

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ragz.modules.auth.models import Invitation, User


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


async def test_superadmin_invites_admin_into_chosen_org(
    client: httpx.AsyncClient, session: AsyncSession, seeded_superadmin: User
) -> None:
    h = await auth(client, "root@platform.example")
    org_r = await client.post("/api/v1/admin/orgs", json={"name": "OtherOrg"}, headers=h)
    assert org_r.status_code == 200
    other_org_id = org_r.json()["id"]

    r = await client.post(
        "/api/v1/auth/invitations",
        json={"email": "newadmin@other.com", "role": "admin", "org_id": other_org_id},
        headers=h,
    )
    assert r.status_code == 201
    token = r.json()["invite_token"]

    invitation = (
        await session.execute(
            select(Invitation).where(Invitation.email == "newadmin@other.com")
        )
    ).scalar_one()
    assert str(invitation.org_id) == other_org_id

    r2 = await client.post(
        "/api/v1/auth/invitations/accept",
        json={"token": token, "password": "newadminpw1234"},
    )
    assert r2.status_code == 201

    new_user = (
        await session.execute(select(User).where(User.email == "newadmin@other.com"))
    ).scalar_one()
    assert str(new_user.org_id) == other_org_id
    assert new_user.role == "admin"


async def test_plain_admin_cannot_invite_into_other_org(
    client: httpx.AsyncClient, session: AsyncSession, seeded_user: User,
    seeded_superadmin: User,
) -> None:
    # seeded_user is an "admin" (not superadmin) in the "Acme" org; target a
    # DIFFERENT org (the superadmin's "Platform" org).
    h = await auth(client, "a@acme.com")
    r = await client.post(
        "/api/v1/auth/invitations",
        json={
            "email": "sneaky@acme.com",
            "role": "admin",
            "org_id": str(seeded_superadmin.org_id),
        },
        headers=h,
    )
    assert r.status_code == 403


async def test_invite_nonexistent_org_404(
    client: httpx.AsyncClient, seeded_superadmin: User
) -> None:
    h = await auth(client, "root@platform.example")
    r = await client.post(
        "/api/v1/auth/invitations",
        json={"email": "ghost@nowhere.com", "role": "admin", "org_id": str(uuid.uuid4())},
        headers=h,
    )
    assert r.status_code == 404
