"""Golden-query admin CRUD routes (Phase 3 §6). Mirrors
tests/api/test_metadata_routes.py's admin-CRUD route shape and
tests/api/test_permissions_routes.py's negative-permission pattern."""

from uuid import UUID

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from ragz.modules.auth.models import User
from ragz.modules.tenancy.models import RoleTemplate, WorkspaceMember


async def auth(client: httpx.AsyncClient, email: str) -> dict[str, str]:
    r = await client.post("/api/v1/auth/login", json={"email": email, "password": "pw123456"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture
async def evals_client(client: httpx.AsyncClient) -> httpx.AsyncClient:
    return client


@pytest.fixture
async def h_admin(evals_client: httpx.AsyncClient, seeded_user: User) -> dict[str, str]:
    return await auth(evals_client, seeded_user.email)


@pytest.fixture
async def ws_id(evals_client: httpx.AsyncClient, h_admin: dict[str, str]) -> str:
    r = await evals_client.post(
        "/api/v1/workspaces", json={"name": "EvalsWS"}, headers=h_admin
    )
    assert r.status_code == 201
    return str(r.json()["id"])


@pytest.fixture
async def h_engineer(
    evals_client: httpx.AsyncClient, seeded_user: User, session: AsyncSession, ws_id: str,
) -> dict[str, str]:
    """A custom-role member WITHOUT workspace.configure -- the never-weaken
    negative-permission case (mirrors test_permissions_routes.py)."""
    template = RoleTemplate(name="Evals Engineer", permissions=["documents.upload", "chat.use"])
    session.add(template)
    await session.flush()
    user = User(
        org_id=seeded_user.org_id, email="engineer-evals@acme.com",
        password_hash=seeded_user.password_hash, role="user", custom_role_id=template.id,
    )
    session.add(user)
    await session.flush()
    session.add(WorkspaceMember(workspace_id=UUID(ws_id), user_id=user.id))
    await session.commit()
    return await auth(evals_client, "engineer-evals@acme.com")


async def test_golden_query_crud_route_lifecycle(evals_client, ws_id, h_admin) -> None:  # type: ignore[no-untyped-def]
    r = await evals_client.post(
        f"/api/v1/workspaces/{ws_id}/golden-queries",
        json={"question": "Where is the muster point?", "expected_document_ids": []},
        headers=h_admin,
    )
    assert r.status_code == 201
    query_id = r.json()["id"]
    r = await evals_client.get(f"/api/v1/workspaces/{ws_id}/golden-queries", headers=h_admin)
    assert len(r.json()) == 1
    r = await evals_client.delete(f"/api/v1/golden-queries/{query_id}", headers=h_admin)
    assert r.status_code == 204


async def test_golden_query_routes_require_configure_permission(
    evals_client, ws_id, h_engineer,
) -> None:  # type: ignore[no-untyped-def]
    r = await evals_client.post(
        f"/api/v1/workspaces/{ws_id}/golden-queries",
        json={"question": "q", "expected_document_ids": []}, headers=h_engineer,
    )
    assert r.status_code == 403


async def test_trigger_and_list_eval_runs(evals_client, ws_id, h_admin, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    enqueued: list[str] = []
    monkeypatch.setattr(
        "ragz.api.routes.evals.enqueue_eval_run", lambda ws, tb: enqueued.append(tb)
    )
    r = await evals_client.post(f"/api/v1/workspaces/{ws_id}/evals/run", headers=h_admin)
    assert r.status_code == 202 and enqueued == ["manual"]
    r = await evals_client.get(f"/api/v1/workspaces/{ws_id}/evals/runs", headers=h_admin)
    assert r.status_code == 200 and r.json() == []


async def test_eval_run_routes_require_configure_permission(
    evals_client, ws_id, h_engineer,
) -> None:  # type: ignore[no-untyped-def]
    r = await evals_client.post(f"/api/v1/workspaces/{ws_id}/evals/run", headers=h_engineer)
    assert r.status_code == 403
    r = await evals_client.get(f"/api/v1/workspaces/{ws_id}/evals/runs", headers=h_engineer)
    assert r.status_code == 403


async def test_trigger_eval_run_rejects_cross_org_workspace(
    evals_client: httpx.AsyncClient, h_admin: dict[str, str], session: AsyncSession, monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    """Task 11 review fix: workspace.configure in Acme must not be able to
    enqueue (and burn LLM/quota budget for) a run against a workspace that
    belongs to a different org, by guessing/observing its UUID. Mirrors
    test_workspaces.py's test_add_member_rejects_cross_org / test_document_approve.py's
    test_approve_cross_org_is_404 second-org fixture pattern."""
    from ragz.modules.tenancy.models import Organization, Workspace

    rival_org = Organization(name="Rival")
    session.add(rival_org)
    await session.flush()
    rival_ws = Workspace(org_id=rival_org.id, name="RivalWS")
    session.add(rival_ws)
    await session.commit()

    enqueued: list[str] = []
    monkeypatch.setattr(
        "ragz.api.routes.evals.enqueue_eval_run", lambda ws, tb: enqueued.append(tb)
    )
    r = await evals_client.post(f"/api/v1/workspaces/{rival_ws.id}/evals/run", headers=h_admin)
    # get_workspace_checked raises WorkspaceAccessDenied (403) uniformly for
    # cross-org and non-member so existence never leaks (tenancy/service.py's
    # own docstring) -- not a 404. That's the real, established status for
    # every route in this file that already goes through this same check.
    assert r.status_code == 403
    assert enqueued == []
