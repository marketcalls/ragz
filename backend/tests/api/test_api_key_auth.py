from collections.abc import AsyncIterator

import httpx
import pytest
from fastapi import APIRouter
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine

from ragz.api.app import create_app
from ragz.api.routes.external import ApiKeyDep
from ragz.core.config import Settings, get_settings
from ragz.core.db import build_session_factory
from ragz.modules.auth.api_keys_service import generate_api_key
from ragz.modules.auth.models import User
from ragz.modules.tenancy.models import Group, UserGroup, Workspace, WorkspaceMember

# Task 4 hasn't landed the real external route yet -- this tiny test-only
# router exercises ApiKeyDep directly, exactly as the brief calls for.
_router = APIRouter()


@_router.get("/__test/api-key-ctx")
async def _ctx(ctx: ApiKeyDep) -> dict:
    return {
        "workspace_ids": sorted(str(w) for w in ctx.workspace_ids),
        "group_ids": sorted(str(g) for g in ctx.group_ids),
        "user_id": str(ctx.user_id),
        "org_id": str(ctx.org_id),
        "role": ctx.role,
    }


def _stub_litellm(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"data": []})


@pytest.fixture
async def api_key_client(
    engine: AsyncEngine, redis_client: Redis, test_settings: Settings
) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(
        session_factory=build_session_factory(engine),
        redis_client=redis_client,
        litellm_transport=httpx.MockTransport(_stub_litellm),
    )
    app.dependency_overrides[get_settings] = lambda: test_settings
    app.include_router(_router)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _make_workspace_and_key(session, settings: Settings, user: User):
    ws = Workspace(org_id=user.org_id, name="WS")
    session.add(ws)
    await session.flush()
    session.add(WorkspaceMember(workspace_id=ws.id, user_id=user.id, role="contributor"))
    group = Group(org_id=user.org_id, name="G1")
    session.add(group)
    await session.flush()
    session.add(UserGroup(group_id=group.id, user_id=user.id))
    await session.commit()
    _, raw = await generate_api_key(
        session, settings, actor_id=user.id, name="k1",
        user_id=user.id, workspace_id=ws.id, expires_at=None,
    )
    return ws, group, raw


async def test_bearer_header_resolves_narrowed_context(
    api_key_client: httpx.AsyncClient, session, seeded_user: User, test_settings: Settings
) -> None:
    ws, group, raw = await _make_workspace_and_key(session, test_settings, seeded_user)
    r = await api_key_client.get(
        "/__test/api-key-ctx", headers={"Authorization": f"Bearer {raw}"}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["workspace_ids"] == [str(ws.id)]
    assert body["group_ids"] == [str(group.id)]
    assert body["user_id"] == str(seeded_user.id)
    assert body["org_id"] == str(seeded_user.org_id)


async def test_x_api_key_header_also_works(
    api_key_client: httpx.AsyncClient, session, seeded_user: User, test_settings: Settings
) -> None:
    ws, group, raw = await _make_workspace_and_key(session, test_settings, seeded_user)
    r = await api_key_client.get("/__test/api-key-ctx", headers={"X-API-Key": raw})
    assert r.status_code == 200
    assert r.json()["workspace_ids"] == [str(ws.id)]


async def test_missing_key_401(api_key_client: httpx.AsyncClient) -> None:
    r = await api_key_client.get("/__test/api-key-ctx")
    assert r.status_code == 401


async def test_garbage_key_401(api_key_client: httpx.AsyncClient) -> None:
    r = await api_key_client.get(
        "/__test/api-key-ctx", headers={"Authorization": "Bearer ragz_sk_not-a-real-key"}
    )
    assert r.status_code == 401


async def test_revoked_key_401(
    api_key_client: httpx.AsyncClient, session, seeded_user: User, test_settings: Settings
) -> None:
    from ragz.modules.auth.api_keys_service import revoke_api_key

    ws = Workspace(org_id=seeded_user.org_id, name="WS2")
    session.add(ws)
    await session.flush()
    session.add(WorkspaceMember(workspace_id=ws.id, user_id=seeded_user.id, role="contributor"))
    await session.commit()
    row, raw = await generate_api_key(
        session, test_settings, actor_id=seeded_user.id, name="k2",
        user_id=seeded_user.id, workspace_id=ws.id, expires_at=None,
    )
    await revoke_api_key(session, key_id=row.id)
    r = await api_key_client.get(
        "/__test/api-key-ctx", headers={"Authorization": f"Bearer {raw}"}
    )
    assert r.status_code == 401


async def test_expired_key_401(
    api_key_client: httpx.AsyncClient, session, seeded_user: User, test_settings: Settings
) -> None:
    # sec RAGZ-PUB-13: an expired key must not authenticate, non-leaking (401
    # -- same generic error as any other bad key, no "expired" detail).
    from datetime import UTC, datetime, timedelta

    ws = Workspace(org_id=seeded_user.org_id, name="WS3")
    session.add(ws)
    await session.flush()
    session.add(WorkspaceMember(workspace_id=ws.id, user_id=seeded_user.id, role="contributor"))
    await session.commit()
    _, raw = await generate_api_key(
        session, test_settings, actor_id=seeded_user.id, name="k3",
        user_id=seeded_user.id, workspace_id=ws.id,
        expires_at=datetime.now(UTC) - timedelta(hours=1),
    )
    r = await api_key_client.get(
        "/__test/api-key-ctx", headers={"Authorization": f"Bearer {raw}"}
    )
    assert r.status_code == 401


async def test_valid_unexpired_key_authenticates_and_passes_rbac02(
    api_key_client: httpx.AsyncClient, session, seeded_user: User, test_settings: Settings
) -> None:
    # sec RAGZ-PUB-13 + RBAC-02: a key within its (now-mandatory) lifetime
    # still authenticates and the RBAC-02 membership/chat.generate
    # revalidation still runs (narrowed context reflects current membership).
    from datetime import UTC, datetime, timedelta

    ws = Workspace(org_id=seeded_user.org_id, name="WS4")
    session.add(ws)
    await session.flush()
    session.add(WorkspaceMember(workspace_id=ws.id, user_id=seeded_user.id, role="contributor"))
    await session.commit()
    _, raw = await generate_api_key(
        session, test_settings, actor_id=seeded_user.id, name="k4",
        user_id=seeded_user.id, workspace_id=ws.id,
        expires_at=datetime.now(UTC) + timedelta(days=1),
    )
    r = await api_key_client.get(
        "/__test/api-key-ctx", headers={"Authorization": f"Bearer {raw}"}
    )
    assert r.status_code == 200
    assert r.json()["workspace_ids"] == [str(ws.id)]
