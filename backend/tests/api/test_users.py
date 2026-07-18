import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from raghub.modules.auth.models import User
from raghub.modules.tenancy.models import Organization


async def auth(client: httpx.AsyncClient, email: str) -> dict[str, str]:
    r = await client.post("/api/v1/auth/login", json={"email": email, "password": "pw123456"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def test_list_and_deactivate(
    client: httpx.AsyncClient, seeded_user: User, session: AsyncSession
) -> None:
    plain = User(org_id=seeded_user.org_id, email="p@acme.com",
                 password_hash=seeded_user.password_hash, role="user")
    session.add(plain)
    await session.commit()

    h = await auth(client, "a@acme.com")
    emails = [u["email"] for u in (await client.get("/api/v1/users", headers=h)).json()]
    assert set(emails) == {"a@acme.com", "p@acme.com"}

    r = await client.patch(f"/api/v1/users/{plain.id}", json={"active": False}, headers=h)
    assert r.status_code == 200 and r.json()["active"] is False
    # deactivated user can no longer log in
    r2 = await client.post(
        "/api/v1/auth/login", json={"email": "p@acme.com", "password": "pw123456"}
    )
    assert r2.status_code == 401


async def test_patch_role_rejects_invalid_value(
    client: httpx.AsyncClient, seeded_user: User, session: AsyncSession
) -> None:
    """UserPatch.role must be a Literal["admin", "user"] - bad values 422 at the boundary."""
    plain = User(org_id=seeded_user.org_id, email="p@acme.com",
                 password_hash=seeded_user.password_hash, role="user")
    session.add(plain)
    await session.commit()

    h = await auth(client, "a@acme.com")
    r = await client.patch(
        f"/api/v1/users/{plain.id}", json={"role": "superadmin"}, headers=h
    )
    assert r.status_code == 422


async def test_empty_body_patch_returns_addressed_user(
    client: httpx.AsyncClient, seeded_user: User, session: AsyncSession
) -> None:
    """An empty-body PATCH must return THE addressed user, not an arbitrary org member."""
    plain = User(org_id=seeded_user.org_id, email="p@acme.com",
                 password_hash=seeded_user.password_hash, role="user")
    session.add(plain)
    await session.commit()

    h = await auth(client, "a@acme.com")
    r = await client.patch(f"/api/v1/users/{plain.id}", json={}, headers=h)
    assert r.status_code == 200
    assert r.json()["email"] == "p@acme.com"


async def test_empty_body_patch_cross_org_user_404s(
    client: httpx.AsyncClient, seeded_user: User, session: AsyncSession
) -> None:
    """Empty-body PATCH on a cross-org user id must 404, not leak another org's user."""
    rival_org = Organization(name="Rival")
    session.add(rival_org)
    await session.flush()
    rival_user = User(
        org_id=rival_org.id, email="r@rival.com",
        password_hash=seeded_user.password_hash, role="user",
    )
    session.add(rival_user)
    await session.commit()

    h = await auth(client, "a@acme.com")
    r = await client.patch(f"/api/v1/users/{rival_user.id}", json={}, headers=h)
    assert r.status_code == 404
