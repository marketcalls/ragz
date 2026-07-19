import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from raghub.modules.auth.models import User


async def auth(client: httpx.AsyncClient, email: str) -> dict[str, str]:
    r = await client.post("/api/v1/auth/login", json={"email": email, "password": "pw123456"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def test_group_admin_flow(
    client: httpx.AsyncClient, seeded_user: User, session: AsyncSession
) -> None:
    plain = User(org_id=seeded_user.org_id, email="p@acme.com",
                 password_hash=seeded_user.password_hash, role="user")
    session.add(plain)
    await session.commit()

    h_admin = await auth(client, "a@acme.com")
    r = await client.post("/api/v1/groups", json={"name": "finance"}, headers=h_admin)
    assert r.status_code == 201
    gid = r.json()["id"]

    assert (
        await client.put(f"/api/v1/groups/{gid}/members/{plain.id}", headers=h_admin)
    ).status_code == 204
    groups = (await client.get("/api/v1/groups", headers=h_admin)).json()
    assert groups[0]["name"] == "finance"
    assert groups[0]["member_ids"] == [str(plain.id)]

    assert (
        await client.delete(f"/api/v1/groups/{gid}/members/{plain.id}", headers=h_admin)
    ).status_code == 204
    assert (await client.delete(f"/api/v1/groups/{gid}", headers=h_admin)).status_code == 204
    assert (await client.get("/api/v1/groups", headers=h_admin)).json() == []


async def test_groups_require_admin(
    client: httpx.AsyncClient, seeded_user: User, session: AsyncSession
) -> None:
    plain = User(org_id=seeded_user.org_id, email="p@acme.com",
                 password_hash=seeded_user.password_hash, role="user")
    session.add(plain)
    await session.commit()
    h_user = await auth(client, "p@acme.com")
    assert (await client.get("/api/v1/groups", headers=h_user)).status_code == 403
    assert (
        await client.post("/api/v1/groups", json={"name": "x"}, headers=h_user)
    ).status_code == 403
