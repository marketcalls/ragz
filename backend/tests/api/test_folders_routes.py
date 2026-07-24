import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from raghub.modules.auth.models import User


async def auth(client: httpx.AsyncClient, email: str) -> dict[str, str]:
    r = await client.post("/api/v1/auth/login", json={"email": email, "password": "pw123456"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def test_create_list_and_rename_folder(
    client: httpx.AsyncClient, seeded_user: User, session: AsyncSession
) -> None:
    h_admin = await auth(client, seeded_user.email)
    r = await client.post("/api/v1/workspaces", json={"name": "Finance"}, headers=h_admin)
    assert r.status_code == 201
    ws_id = r.json()["id"]

    r = await client.post(
        f"/api/v1/workspaces/{ws_id}/folders",
        json={"name": "Legal", "parent_folder_id": None},
        headers=h_admin,
    )
    assert r.status_code == 201
    folder = r.json()
    assert folder["name"] == "Legal"
    assert folder["workspace_id"] == ws_id
    assert folder["parent_folder_id"] is None
    folder_id = folder["id"]

    r = await client.get(f"/api/v1/workspaces/{ws_id}/folders", headers=h_admin)
    assert r.status_code == 200
    names = [f["name"] for f in r.json()]
    assert names == ["Legal"]

    r = await client.patch(
        f"/api/v1/folders/{folder_id}", json={"name": "Legal Docs"}, headers=h_admin
    )
    assert r.status_code == 200
    assert r.json()["name"] == "Legal Docs"


async def test_move_folder_into_own_descendant_returns_409_problem_json(
    client: httpx.AsyncClient, seeded_user: User, session: AsyncSession
) -> None:
    h_admin = await auth(client, seeded_user.email)
    r = await client.post("/api/v1/workspaces", json={"name": "Ops"}, headers=h_admin)
    assert r.status_code == 201
    ws_id = r.json()["id"]

    r = await client.post(
        f"/api/v1/workspaces/{ws_id}/folders",
        json={"name": "Parent"},
        headers=h_admin,
    )
    parent_id = r.json()["id"]

    r = await client.post(
        f"/api/v1/workspaces/{ws_id}/folders",
        json={"name": "Child", "parent_folder_id": parent_id},
        headers=h_admin,
    )
    child_id = r.json()["id"]

    r = await client.patch(
        f"/api/v1/folders/{parent_id}",
        json={"parent_folder_id": child_id},
        headers=h_admin,
    )
    assert r.status_code == 409
    assert r.headers["content-type"] == "application/problem+json"
