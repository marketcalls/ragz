"""sec RAGZ-PUB-01 acceptance proof: every route now ENFORCES exactly the
granular action it DECLARES in api/policy.py. Before this fix several routes
were gated on a broader/unrelated action (or auth-only), so a custom role
DENIED the specific granular action could still perform it -- most starkly the
combined PATCH /documents/{id} that took auth-only yet both pinned AND moved a
document.

Each test builds a custom role that ALLOWS the surrounding resource read (so the
403 is decided by the missing WRITE action, not by workspace membership or an
inability to see the resource) but DENIES exactly the action under test, then
asserts the route returns 403 AND that no state change / no queued job happened.
"""
from typing import Any

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ragz.modules.auth.models import User
from ragz.modules.documents.models import Document
from ragz.modules.tenancy.reembed_models import ReembedJob
from tests.api.test_permissions_routes import make_templated_member

# The read floor granted to every negative-test role -- enough to see the
# workspace/document/folder, deliberately WITHOUT any of the write actions the
# individual tests probe for.
_READ_FLOOR = [
    "workspace.read", "documents.list", "documents.content.read",
    "folders.read", "search.execute", "chat.read",
]


async def auth(client: httpx.AsyncClient, email: str) -> dict[str, str]:
    r = await client.post("/api/v1/auth/login", json={"email": email, "password": "pw123456"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _reader(
    session: AsyncSession, seeded_user: User, ws_id: str, *, email: str, extra: list[str]
) -> None:
    """A custom-role workspace member holding the read floor plus `extra`."""
    await make_templated_member(
        session, seeded_user, email=email, template_name=f"role-{email}",
        permissions=[*_READ_FLOOR, *extra], workspace_id=ws_id,
    )


async def test_pin_denied_without_documents_pin(
    client: httpx.AsyncClient, seeded_user: User, session: AsyncSession,
    chat_env: dict[str, Any],
) -> None:
    ws, doc = chat_env["workspace"], chat_env["document"]
    assert doc.pinned is False
    await _reader(session, seeded_user, str(ws.id), email="nopin@acme.com", extra=[])
    h = await auth(client, "nopin@acme.com")

    r = await client.patch(f"/api/v1/documents/{doc.id}/pin", json={"pinned": True}, headers=h)
    assert r.status_code == 403
    assert "requires permission documents.pin" in r.json()["detail"]

    fresh = await session.get(Document, doc.id)
    assert fresh is not None
    await session.refresh(fresh)
    assert fresh.pinned is False


async def test_move_denied_without_documents_move(
    client: httpx.AsyncClient, seeded_user: User, session: AsyncSession,
    chat_env: dict[str, Any],
) -> None:
    ws, doc = chat_env["workspace"], chat_env["document"]
    h_admin = await auth(client, seeded_user.email)
    r = await client.post(
        f"/api/v1/workspaces/{ws.id}/folders", json={"name": "Target"}, headers=h_admin
    )
    assert r.status_code == 201
    folder_id = r.json()["id"]

    # A role that CAN pin but is denied move -- proves the split gates are
    # independent (the old combined endpoint would have let this through).
    await _reader(session, seeded_user, str(ws.id), email="nomove@acme.com",
                  extra=["documents.pin"])
    h = await auth(client, "nomove@acme.com")

    r = await client.patch(
        f"/api/v1/documents/{doc.id}/move", json={"folder_id": folder_id}, headers=h
    )
    assert r.status_code == 403
    assert "requires permission documents.move" in r.json()["detail"]

    fresh = await session.get(Document, doc.id)
    assert fresh is not None
    await session.refresh(fresh)
    assert fresh.folder_id is None


async def test_patch_folder_denied_without_folders_update(
    client: httpx.AsyncClient, seeded_user: User, session: AsyncSession,
) -> None:
    h_admin = await auth(client, seeded_user.email)
    r = await client.post("/api/v1/workspaces", json={"name": "FolderWS"}, headers=h_admin)
    ws_id = r.json()["id"]
    r = await client.post(
        f"/api/v1/workspaces/{ws_id}/folders", json={"name": "Original"}, headers=h_admin
    )
    folder_id = r.json()["id"]

    await _reader(session, seeded_user, ws_id, email="nofolderupd@acme.com", extra=[])
    h = await auth(client, "nofolderupd@acme.com")

    r = await client.patch(
        f"/api/v1/folders/{folder_id}", json={"name": "Hijacked"}, headers=h
    )
    assert r.status_code == 403
    assert "requires permission folders.update" in r.json()["detail"]

    r = await client.get(f"/api/v1/workspaces/{ws_id}/folders", headers=h_admin)
    assert [f["name"] for f in r.json()] == ["Original"]


async def test_create_metadata_field_denied_without_workspace_metadata_manage(
    client: httpx.AsyncClient, seeded_user: User, session: AsyncSession,
    chat_env: dict[str, Any], stack_env: None,
) -> None:
    ws = chat_env["workspace"]
    h_admin = await auth(client, seeded_user.email)
    before = await client.get(f"/api/v1/workspaces/{ws.id}/metadata-fields", headers=h_admin)
    before_names = {f["name"] for f in before.json()}

    await _reader(session, seeded_user, str(ws.id), email="nometafield@acme.com", extra=[])
    h = await auth(client, "nometafield@acme.com")

    r = await client.post(
        f"/api/v1/workspaces/{ws.id}/metadata-fields", headers=h,
        json={"name": "sneaky", "label": "Sneaky", "field_type": "text"},
    )
    assert r.status_code == 403
    assert "requires permission workspace.metadata.manage" in r.json()["detail"]

    after = await client.get(f"/api/v1/workspaces/{ws.id}/metadata-fields", headers=h_admin)
    assert {f["name"] for f in after.json()} == before_names


async def test_set_document_metadata_denied_without_documents_metadata_update(
    client: httpx.AsyncClient, seeded_user: User, session: AsyncSession,
    chat_env: dict[str, Any], stack_env: None,
) -> None:
    ws, doc = chat_env["workspace"], chat_env["document"]
    # Seed the preset fields so the payload keys are otherwise valid.
    h_admin = await auth(client, seeded_user.email)
    await client.get(f"/api/v1/workspaces/{ws.id}/metadata-fields", headers=h_admin)

    # Granting documents.upload proves the fix: the metadata PUT used to ride on
    # documents.upload, so an uploader could edit metadata; now it must not.
    await _reader(session, seeded_user, str(ws.id), email="nometaval@acme.com",
                  extra=["documents.upload"])
    h = await auth(client, "nometaval@acme.com")

    r = await client.put(
        f"/api/v1/documents/{doc.id}/metadata", headers=h,
        json={"values": {"department": "engineering"}},
    )
    assert r.status_code == 403
    assert "requires permission documents.metadata.update" in r.json()["detail"]

    fresh = await session.get(Document, doc.id)
    assert fresh is not None
    await session.refresh(fresh)
    assert not fresh.meta


async def test_eval_run_denied_without_evals_run(
    client: httpx.AsyncClient, seeded_user: User, session: AsyncSession,
    chat_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
) -> None:
    ws = chat_env["workspace"]
    enqueued: list[str] = []
    monkeypatch.setattr(
        "ragz.api.routes.evals.enqueue_eval_run", lambda w, tb: enqueued.append(tb)
    )
    # evals.read (view runs) granted, evals.run (trigger) denied -- the actions
    # were indistinguishable when both routes shared workspace.configure.
    await _reader(session, seeded_user, str(ws.id), email="noevalrun@acme.com",
                  extra=["evals.read"])
    h = await auth(client, "noevalrun@acme.com")

    r = await client.post(f"/api/v1/workspaces/{ws.id}/evals/run", headers=h)
    assert r.status_code == 403
    assert "requires permission evals.run" in r.json()["detail"]
    assert enqueued == []


async def test_reembed_denied_without_workspace_reembed(
    client: httpx.AsyncClient, seeded_user: User, session: AsyncSession,
    chat_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
) -> None:
    ws = chat_env["workspace"]
    calls: list[tuple] = []  # type: ignore[type-arg]
    monkeypatch.setattr(
        "ragz.api.routes.workspaces.enqueue_reembed_workspace",
        lambda *a: calls.append(a),
    )
    # workspace.configure granted but workspace.reembed denied: re-embed used to
    # ride on workspace.configure, so a configurer could trigger a full re-embed.
    await _reader(session, seeded_user, str(ws.id), email="noreembed@acme.com",
                  extra=["workspace.configure"])
    h = await auth(client, "noreembed@acme.com")

    r = await client.post(
        f"/api/v1/workspaces/{ws.id}/reembed",
        json={"new_embedding_model_id": "00000000-0000-0000-0000-000000000123"}, headers=h,
    )
    assert r.status_code == 403
    assert "requires permission workspace.reembed" in r.json()["detail"]
    assert calls == []

    jobs = (
        await session.execute(select(ReembedJob).where(ReembedJob.workspace_id == ws.id))
    ).scalars().all()
    assert jobs == []


async def test_list_workspaces_denied_without_workspace_read(
    client: httpx.AsyncClient, seeded_user: User, session: AsyncSession,
) -> None:
    # GET /workspaces was auth-only (CtxDep); it now enforces workspace.read.
    await make_templated_member(
        session, seeded_user, email="noread@acme.com", template_name="NoWsRead",
        permissions=["chat.read"],
    )
    h = await auth(client, "noread@acme.com")
    r = await client.get("/api/v1/workspaces", headers=h)
    assert r.status_code == 403
    assert "requires permission workspace.read" in r.json()["detail"]


async def test_golden_query_list_denied_without_evals_read(
    client: httpx.AsyncClient, seeded_user: User, session: AsyncSession,
    chat_env: dict[str, Any],
) -> None:
    # GET golden-queries declares evals.read; a role with evals.manage-less,
    # evals.read-less read floor must be denied.
    ws = chat_env["workspace"]
    await _reader(session, seeded_user, str(ws.id), email="noevalread@acme.com", extra=[])
    h = await auth(client, "noevalread@acme.com")
    r = await client.get(f"/api/v1/workspaces/{ws.id}/golden-queries", headers=h)
    assert r.status_code == 403
    assert "requires permission evals.read" in r.json()["detail"]
