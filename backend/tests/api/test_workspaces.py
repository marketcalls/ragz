import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from raghub.modules.auth.models import User


async def auth(client: httpx.AsyncClient, email: str) -> dict[str, str]:
    r = await client.post("/api/v1/auth/login", json={"email": email, "password": "pw123456"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def test_admin_creates_workspace_and_adds_member(
    client: httpx.AsyncClient, seeded_user: User, session: AsyncSession
) -> None:
    plain = User(
        org_id=seeded_user.org_id, email="p@acme.com",
        password_hash=seeded_user.password_hash, role="user",
    )
    session.add(plain)
    await session.commit()

    h_admin = await auth(client, "a@acme.com")
    r = await client.post("/api/v1/workspaces", json={"name": "Finance"}, headers=h_admin)
    assert r.status_code == 201
    ws_id = r.json()["id"]

    h_user = await auth(client, "p@acme.com")
    # not a member yet -> sees nothing
    assert (await client.get("/api/v1/workspaces", headers=h_user)).json() == []
    # plain user cannot create
    assert (
        await client.post("/api/v1/workspaces", json={"name": "X"}, headers=h_user)
    ).status_code == 403

    r = await client.post(
        f"/api/v1/workspaces/{ws_id}/members",
        json={"user_id": str(plain.id)}, headers=h_admin,
    )
    assert r.status_code == 204
    names = [w["name"] for w in (await client.get("/api/v1/workspaces", headers=h_user)).json()]
    assert names == ["Finance"]
