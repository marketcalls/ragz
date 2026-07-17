import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from raghub.modules.auth.models import User
from raghub.modules.tenancy.models import Organization, Workspace


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


async def test_add_member_rejects_cross_org(
    client: httpx.AsyncClient, seeded_user: User, session: AsyncSession
) -> None:
    """Prove POST /api/v1/workspaces/{id}/members cannot bridge organizations."""
    # Create a second org "Rival" with its own user
    rival_org = Organization(name="Rival")
    session.add(rival_org)
    await session.flush()

    rival_user = User(
        org_id=rival_org.id,
        email="r@rival.com",
        password_hash=seeded_user.password_hash,
        role="user",
    )
    session.add(rival_user)
    await session.commit()

    # Login as Acme admin and create a workspace in Acme
    h_acme_admin = await auth(client, "a@acme.com")
    r = await client.post("/api/v1/workspaces", json={"name": "AcmeWS"}, headers=h_acme_admin)
    assert r.status_code == 201
    acme_ws_id = r.json()["id"]

    # Try to add Rival user to Acme workspace - should get 404
    r = await client.post(
        f"/api/v1/workspaces/{acme_ws_id}/members",
        json={"user_id": str(rival_user.id)},
        headers=h_acme_admin,
    )
    assert r.status_code == 404
    assert r.headers["content-type"] == "application/problem+json"
    assert "Rival" not in r.json()["detail"]

    # Create a workspace under Rival org directly
    rival_ws = Workspace(org_id=rival_org.id, name="RivalWS")
    session.add(rival_ws)
    await session.commit()

    # Try to add Acme user to Rival workspace - should get 404
    r = await client.post(
        f"/api/v1/workspaces/{rival_ws.id}/members",
        json={"user_id": str(seeded_user.id)},
        headers=h_acme_admin,
    )
    assert r.status_code == 404
    assert r.headers["content-type"] == "application/problem+json"
    assert "Rival" not in r.json()["detail"]
