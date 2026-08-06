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
async def ws_and_member(session: AsyncSession, seeded_user: User) -> tuple[UUID, UUID]:
    ws = Workspace(org_id=seeded_user.org_id, name="BotAdminWS")
    session.add(ws)
    await session.flush()
    session.add(WorkspaceMember(workspace_id=ws.id, user_id=seeded_user.id))
    await session.commit()
    return ws.id, seeded_user.id


async def test_create_returns_masked_out_with_webhook_url_no_credentials(
    client: httpx.AsyncClient, super_headers: dict[str, str], ws_and_member: tuple[UUID, UUID],
) -> None:
    ws_id, user_id = ws_and_member
    r = await client.post(
        "/api/v1/admin/bots", headers=super_headers,
        json={
            "platform": "telegram", "name": "support-bot", "workspace_id": str(ws_id),
            "user_id": str(user_id), "token": "123:ABC-secret-token", "signing_secret": "tg-secret",
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert "token" not in body and "signing_secret" not in body  # never returned, not even once
    assert body["webhook_id"]  # non-empty, usable value
    assert body["webhook_url"].endswith(f"/external/bots/telegram/{body['webhook_id']}")
    lst = (await client.get("/api/v1/admin/bots", headers=super_headers)).json()
    assert all("token" not in b and "signing_secret" not in b for b in lst)


async def test_patch_enabled_toggle(
    client: httpx.AsyncClient, super_headers: dict[str, str], ws_and_member: tuple[UUID, UUID],
) -> None:
    ws_id, user_id = ws_and_member
    bot_id = (
        await client.post(
            "/api/v1/admin/bots", headers=super_headers,
            json={
                "platform": "slack", "name": "s", "workspace_id": str(ws_id),
                "user_id": str(user_id), "token": "xoxb-tok", "signing_secret": "sig",
            },
        )
    ).json()["id"]
    r = await client.patch(
        f"/api/v1/admin/bots/{bot_id}", headers=super_headers, json={"enabled": False}
    )
    assert r.status_code == 200
    assert r.json()["enabled"] is False


async def test_delete(
    client: httpx.AsyncClient, super_headers: dict[str, str], ws_and_member: tuple[UUID, UUID],
) -> None:
    ws_id, user_id = ws_and_member
    bot_id = (
        await client.post(
            "/api/v1/admin/bots", headers=super_headers,
            json={
                "platform": "discord", "name": "d", "workspace_id": str(ws_id),
                "user_id": str(user_id), "token": "bot-tok", "signing_secret": "0" * 64,
            },
        )
    ).json()["id"]
    r = await client.delete(f"/api/v1/admin/bots/{bot_id}", headers=super_headers)
    assert r.status_code == 204
    assert (await client.get("/api/v1/admin/bots", headers=super_headers)).json() == []


async def test_requires_superadmin(
    client: httpx.AsyncClient, member_headers: dict[str, str]
) -> None:
    assert (await client.get("/api/v1/admin/bots", headers=member_headers)).status_code == 403
    assert (
        await client.post(
            "/api/v1/admin/bots", headers=member_headers,
            json={
                "platform": "telegram", "name": "x", "workspace_id": str(UUID(int=0)),
                "user_id": str(UUID(int=0)), "token": "t", "signing_secret": "s",
            },
        )
    ).status_code == 403
