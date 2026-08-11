from uuid import UUID

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from ragz.modules.auth.models import User
from ragz.modules.tenancy.models import Workspace, WorkspaceMember


async def auth(client: httpx.AsyncClient, email: str) -> dict[str, str]:
    r = await client.post("/api/v1/auth/login", json={"email": email, "password": "pw123456"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture
async def super_headers(client: httpx.AsyncClient, seeded_superadmin: User) -> dict[str, str]:
    return await auth(client, seeded_superadmin.email)


@pytest.fixture
async def member_headers(client: httpx.AsyncClient, seeded_user: User) -> dict[str, str]:
    return await auth(client, seeded_user.email)


@pytest.fixture
async def ws_and_member(
    session: AsyncSession, seeded_user: User
) -> tuple[UUID, UUID]:
    ws = Workspace(org_id=seeded_user.org_id, name="ApiKeyWS")
    session.add(ws)
    await session.flush()
    session.add(WorkspaceMember(workspace_id=ws.id, user_id=seeded_user.id))
    await session.commit()
    return ws.id, seeded_user.id


async def test_generate_returns_raw_once_then_masked(
    client: httpx.AsyncClient, super_headers: dict[str, str],
    ws_and_member: tuple[UUID, UUID],
) -> None:
    ws_id, user_id = ws_and_member
    r = await client.post(
        "/api/v1/admin/api-keys", headers=super_headers,
        json={"name": "k1", "user_id": str(user_id), "workspace_id": str(ws_id)},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["api_key"].startswith("ragz_sk_")  # raw ONLY here
    assert body["prefix"] == body["api_key"][:12]
    lst = (await client.get("/api/v1/admin/api-keys", headers=super_headers)).json()
    assert all("api_key" not in k and "key_hash" not in k for k in lst)  # never in list


async def test_revoke(
    client: httpx.AsyncClient, super_headers: dict[str, str],
    ws_and_member: tuple[UUID, UUID],
) -> None:
    ws_id, user_id = ws_and_member
    kid = (
        await client.post(
            "/api/v1/admin/api-keys", headers=super_headers,
            json={"name": "k", "user_id": str(user_id), "workspace_id": str(ws_id)},
        )
    ).json()["id"]
    assert (
        await client.delete(f"/api/v1/admin/api-keys/{kid}", headers=super_headers)
    ).status_code == 204


async def test_requires_superadmin(
    client: httpx.AsyncClient, member_headers: dict[str, str],
) -> None:
    assert (
        await client.get("/api/v1/admin/api-keys", headers=member_headers)
    ).status_code == 403
    assert (
        await client.post(
            "/api/v1/admin/api-keys", headers=member_headers,
            json={"name": "x", "user_id": str(UUID(int=0)), "workspace_id": str(UUID(int=0))},
        )
    ).status_code == 403


async def test_revoke_nonexistent_key_404s_not_silent_success(
    client: httpx.AsyncClient, super_headers: dict[str, str],
) -> None:
    import uuid
    r = await client.delete(f"/api/v1/admin/api-keys/{uuid.uuid4()}", headers=super_headers)
    assert r.status_code == 404


async def test_revoke_records_the_keys_own_org_not_actors(
    client: httpx.AsyncClient, super_headers: dict[str, str],
    ws_and_member: tuple[UUID, UUID], session: AsyncSession,
) -> None:
    ws_id, user_id = ws_and_member
    key_id = (await client.post(
        "/api/v1/admin/api-keys", headers=super_headers,
        json={"name": "k", "user_id": str(user_id), "workspace_id": str(ws_id)},
    )).json()["id"]
    await client.delete(f"/api/v1/admin/api-keys/{key_id}", headers=super_headers)
    from sqlalchemy import select

    from ragz.modules.audit.models import AuditEvent
    key_owner_org = (
        await session.execute(select(User.org_id).where(User.id == user_id))
    ).scalar_one()
    event = (
        await session.execute(
            select(AuditEvent).where(AuditEvent.action == "api_key.revoked")
        )
    ).scalars().first()
    assert event is not None
    assert event.org_id == key_owner_org
