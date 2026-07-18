import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from raghub.modules.auth.models import User


async def auth(client: httpx.AsyncClient, email: str) -> dict[str, str]:
    r = await client.post("/api/v1/auth/login", json={"email": email, "password": "pw123456"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def make_workspace(client: httpx.AsyncClient, h: dict[str, str]) -> str:
    r = await client.post("/api/v1/workspaces", json={"name": "Tuned"}, headers=h)
    assert r.status_code == 201
    return str(r.json()["id"])


async def test_new_workspace_has_retrieval_defaults(
    client: httpx.AsyncClient, seeded_user: User
) -> None:
    h = await auth(client, "a@acme.com")
    ws_id = await make_workspace(client, h)
    ws = next(w for w in (await client.get("/api/v1/workspaces", headers=h)).json()
              if w["id"] == ws_id)
    assert ws["top_k"] == 8
    assert ws["rerank_enabled"] is False
    assert ws["system_prompt_override"] is None


async def test_admin_updates_retrieval_settings(
    client: httpx.AsyncClient, seeded_user: User
) -> None:
    h = await auth(client, "a@acme.com")
    ws_id = await make_workspace(client, h)
    r = await client.patch(
        f"/api/v1/workspaces/{ws_id}",
        json={"top_k": 12, "min_score": 0.5, "rerank_enabled": True,
              "system_prompt_override": "Answer in formal English."},
        headers=h,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["top_k"] == 12 and body["min_score"] == 0.5
    assert body["rerank_enabled"] is True
    assert body["system_prompt_override"] == "Answer in formal English."


async def test_explicit_null_clears_prompt_override_only(
    client: httpx.AsyncClient, seeded_user: User
) -> None:
    h = await auth(client, "a@acme.com")
    ws_id = await make_workspace(client, h)
    await client.patch(f"/api/v1/workspaces/{ws_id}",
                       json={"system_prompt_override": "x"}, headers=h)
    r = await client.patch(f"/api/v1/workspaces/{ws_id}",
                           json={"system_prompt_override": None}, headers=h)
    assert r.status_code == 200 and r.json()["system_prompt_override"] is None
    # null for a non-nullable setting is a 409, not a silent no-op
    r2 = await client.patch(f"/api/v1/workspaces/{ws_id}", json={"top_k": None}, headers=h)
    assert r2.status_code == 409


async def test_top_k_bounds_enforced(client: httpx.AsyncClient, seeded_user: User) -> None:
    h = await auth(client, "a@acme.com")
    ws_id = await make_workspace(client, h)
    assert (await client.patch(f"/api/v1/workspaces/{ws_id}", json={"top_k": 0},
                               headers=h)).status_code == 422
    assert (await client.patch(f"/api/v1/workspaces/{ws_id}", json={"top_k": 51},
                               headers=h)).status_code == 422


async def test_non_admin_cannot_patch(
    client: httpx.AsyncClient, seeded_user: User, session: AsyncSession
) -> None:
    plain = User(org_id=seeded_user.org_id, email="p@acme.com",
                 password_hash=seeded_user.password_hash, role="user")
    session.add(plain)
    await session.commit()
    h_admin = await auth(client, "a@acme.com")
    ws_id = await make_workspace(client, h_admin)
    h_user = await auth(client, "p@acme.com")
    r = await client.patch(f"/api/v1/workspaces/{ws_id}", json={"top_k": 5}, headers=h_user)
    assert r.status_code == 403
