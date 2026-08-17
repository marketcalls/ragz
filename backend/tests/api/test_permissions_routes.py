"""Task 13 (RBAC-2): granular permission guards on documents/workspaces/usage/chat
routes.

The never-weaken proof lives in the PRE-EXISTING suites (test_documents_routes.py,
test_workspace_settings.py, test_workspaces.py, chat/usage tests) passing UNMODIFIED --
every legacy 403 stays a 403 because DEFAULT_USER_PERMISSIONS mirrors pre-Plan-H
user-tier behavior exactly. This file proves the NEW capability: a custom-role
holder can be narrowed below (engineer loses documents.delete) or widened above
(HSE manager gains analytics/workspace.configure) that legacy baseline.
"""
from typing import Any
from uuid import UUID

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from ragz.modules.auth.models import User
from ragz.modules.outbox import service as outbox_service
from ragz.modules.tenancy.models import RoleTemplate, WorkspaceMember
from tests.api.test_chat_stream import auth, make_model_and_chat

# chat_client/fake_streamer live in test_chat_stream; pytest only shares fixtures
# across modules via conftest.py or an explicit plugin import (mirrors
# test_chat_isolation.py's use of the same pattern).
pytest_plugins = ["tests.api.test_chat_stream"]


@pytest.fixture
def captured_enqueues(monkeypatch: pytest.MonkeyPatch) -> dict[str, list]:  # type: ignore[type-arg]
    """Record enqueued background work.

    Ingest is no longer a direct enqueue_ingest() call: create_from_upload
    publishes an OUTBOX event inside its own transaction (review P1), and the
    route only nudges the dispatcher afterwards. Spying on publish keeps these
    assertions meaning what they always meant -- "uploading this document owed
    ingest work" -- while going through the real durable path.
    """
    calls: dict[str, list] = {"ingest": [], "delete": [], "reindex": []}  # type: ignore[type-arg]
    real_publish = outbox_service.publish

    def _spy_publish(session, *, topic, payload, queue="default"):  # type: ignore[no-untyped-def]
        if topic == "documents.ingest":
            calls["ingest"].append((UUID(payload["document_id"]), payload["size_bytes"]))
        elif topic == "documents.delete":
            calls["delete"].append(
                (UUID(payload["document_id"]), UUID(payload["actor_id"]))
            )
        elif topic == "documents.reindex":
            calls["reindex"].append(UUID(payload["document_id"]))
        return real_publish(session, topic=topic, payload=payload, queue=queue)

    monkeypatch.setattr(outbox_service, "publish", _spy_publish)
    # The nudge is pure latency optimisation; the event is already durable.
    async def _noop_dispatch(*_a: object, **_k: object) -> int:
        return 0

    monkeypatch.setattr("ragz.api.routes.documents.dispatch_pending", _noop_dispatch)
    return calls


async def make_workspace(client: httpx.AsyncClient, h: dict[str, str]) -> str:
    r = await client.post("/api/v1/workspaces", json={"name": "Rig"}, headers=h)
    assert r.status_code == 201
    return str(r.json()["id"])


async def make_templated_member(
    session: AsyncSession, seeded_user: User, *, email: str,
    template_name: str, permissions: list[str], workspace_id: str | None = None,
) -> User:
    """A role="user" account with a custom RoleTemplate assigned, optionally
    dropped into a workspace as a member (mirrors service.add_member's row,
    without going through the AdminDep route)."""
    template = RoleTemplate(name=template_name, permissions=permissions)
    session.add(template)
    await session.flush()
    user = User(org_id=seeded_user.org_id, email=email,
                password_hash=seeded_user.password_hash, role="user",
                custom_role_id=template.id)
    session.add(user)
    await session.flush()
    if workspace_id is not None:
        session.add(WorkspaceMember(workspace_id=UUID(workspace_id), user_id=user.id))
    await session.commit()
    return user


async def test_engineer_uploads_but_cannot_delete(
    client: httpx.AsyncClient, seeded_user: User, session: AsyncSession, stack_env: None,
    captured_enqueues: dict,  # type: ignore[type-arg]
) -> None:
    h_admin = await auth(client, "a@acme.com")
    ws_id = await make_workspace(client, h_admin)
    await make_templated_member(
        session, seeded_user, email="engineer@acme.com", template_name="Engineer",
        permissions=["documents.upload", "chat.use"], workspace_id=ws_id,
    )
    h_engineer = await auth(client, "engineer@acme.com")

    r = await client.post(
        f"/api/v1/workspaces/{ws_id}/documents", headers=h_engineer,
        files={"file": ("notes.txt", b"pipe stress report", "text/plain")},
    )
    assert r.status_code == 201
    doc_id = r.json()["id"]

    r2 = await client.delete(f"/api/v1/documents/{doc_id}", headers=h_engineer)
    assert r2.status_code == 403
    assert r2.headers["content-type"] == "application/problem+json"
    assert "requires permission documents.delete" in r2.json()["detail"]
    assert captured_enqueues["delete"] == []


async def test_default_user_is_non_destructive_and_explicit_role_restores(
    chat_client: httpx.AsyncClient, chat_env: dict[str, Any], session: AsyncSession,
    seeded_user: User, seeded_superadmin: User, stack_env: None,
    captured_enqueues: dict,  # type: ignore[type-arg]
) -> None:
    """RBAC-04 deny-by-default: a plain role="user" account (no custom role)
    keeps only the NON-DESTRUCTIVE floor -- it can open a chat and send
    messages (chat.generate) -- but can NO LONGER upload or delete (those left
    DEFAULT_USER_PERMISSIONS). An explicit role carrying documents.upload/
    documents.delete restores exactly those, proving capability is now
    grant-based, not ambient. (Before RBAC-04 the plain account had upload/
    delete/chat ambiently; removing that ambient power is the whole point.)"""
    ws = chat_env["workspace"]
    plain = User(org_id=seeded_user.org_id, email="plain@acme.com",
                password_hash=seeded_user.password_hash, role="user")
    session.add(plain)
    await session.flush()
    session.add(WorkspaceMember(workspace_id=ws.id, user_id=plain.id))
    await session.commit()

    h_admin = await auth(chat_client, "a@acme.com")
    h_plain = await auth(chat_client, "plain@acme.com")
    # make_model_and_chat sets the workspace's default model (as admin) and
    # opens a throwaway chat; get_chat scopes by ctx.user_id too, so the chat
    # this test actually sends against must be opened by the plain user itself.
    await make_model_and_chat(chat_client, chat_env, session, seeded_superadmin, h_admin)

    # Non-destructive floor intact: the plain user can still open + send a chat.
    r_own_chat = await chat_client.post(
        "/api/v1/chats", json={"workspace_id": str(ws.id)}, headers=h_plain
    )
    assert r_own_chat.status_code == 201
    chat_id = r_own_chat.json()["id"]
    r_send = await chat_client.post(
        f"/api/v1/chats/{chat_id}/messages", json={"content": "hi"}, headers=h_plain
    )
    assert r_send.status_code == 200
    assert r_send.headers["content-type"].startswith("text/event-stream")

    # Deny-by-default: no ambient upload for a role-less user.
    r_upload_denied = await chat_client.post(
        f"/api/v1/workspaces/{ws.id}/documents", headers=h_plain,
        files={"file": ("nope.txt", b"no ambient upload", "text/plain")},
    )
    assert r_upload_denied.status_code == 403

    # An explicit role restores upload + delete (grant-based capability).
    await make_templated_member(
        session, seeded_user, email="contrib@acme.com",
        template_name="Contributor-test",
        permissions=["documents.upload", "documents.delete", "chat.generate"],
        workspace_id=str(ws.id),
    )
    h_contrib = await auth(chat_client, "contrib@acme.com")
    r_upload = await chat_client.post(
        f"/api/v1/workspaces/{ws.id}/documents", headers=h_contrib,
        files={"file": ("ok.txt", b"granted upload", "text/plain")},
    )
    assert r_upload.status_code == 201
    doc_id = r_upload.json()["id"]
    r_delete = await chat_client.delete(f"/api/v1/documents/{doc_id}", headers=h_contrib)
    assert r_delete.status_code == 202
    assert len(captured_enqueues["delete"]) == 1


async def test_hse_manager_reads_analytics(
    client: httpx.AsyncClient, seeded_user: User, session: AsyncSession,
) -> None:
    await make_templated_member(
        session, seeded_user, email="hse@acme.com", template_name="HSE Manager",
        permissions=["documents.upload", "documents.delete", "chat.use",
                     "analytics.view", "workspace.configure"],
    )
    plain = User(org_id=seeded_user.org_id, email="plain2@acme.com",
                password_hash=seeded_user.password_hash, role="user")
    session.add(plain)
    await session.commit()

    h_hse = await auth(client, "hse@acme.com")
    r = await client.get("/api/v1/admin/usage/summary", headers=h_hse)
    assert r.status_code == 200

    h_plain = await auth(client, "plain2@acme.com")
    r2 = await client.get("/api/v1/admin/usage/summary", headers=h_plain)
    assert r2.status_code == 403
    assert "requires permission analytics.view" in r2.json()["detail"]


async def test_hse_manager_configures_workspace(
    client: httpx.AsyncClient, seeded_user: User, session: AsyncSession,
) -> None:
    h_admin = await auth(client, "a@acme.com")
    ws_id = await make_workspace(client, h_admin)

    await make_templated_member(
        session, seeded_user, email="hse2@acme.com", template_name="HSE Manager 2",
        permissions=["documents.upload", "documents.delete", "chat.use",
                     "analytics.view", "workspace.configure"],
        workspace_id=ws_id,
    )
    await make_templated_member(
        session, seeded_user, email="engineer2@acme.com", template_name="Engineer 2",
        permissions=["documents.upload", "chat.use"], workspace_id=ws_id,
    )

    h_hse = await auth(client, "hse2@acme.com")
    r_hse = await client.patch(f"/api/v1/workspaces/{ws_id}", json={"top_k": 12}, headers=h_hse)
    assert r_hse.status_code == 200
    assert r_hse.json()["top_k"] == 12

    h_engineer = await auth(client, "engineer2@acme.com")
    r_engineer = await client.patch(
        f"/api/v1/workspaces/{ws_id}", json={"top_k": 5}, headers=h_engineer
    )
    assert r_engineer.status_code == 403
    assert "requires permission workspace.configure" in r_engineer.json()["detail"]

    r_admin = await client.patch(f"/api/v1/workspaces/{ws_id}", json={"top_k": 9}, headers=h_admin)
    assert r_admin.status_code == 200


async def test_role_without_chat_generate_blocked(
    chat_client: httpx.AsyncClient, chat_env: dict[str, Any], session: AsyncSession,
    seeded_user: User, seeded_superadmin: User,
) -> None:
    # RBAC-04/RBAC-03: the chat send gate is now chat.generate (the granular
    # successor to the retired chat.use flag). A custom role that grants some
    # other action but not chat.generate is still blocked from sending.
    ws = chat_env["workspace"]
    h_admin = await auth(chat_client, "a@acme.com")
    chat_id = await make_model_and_chat(chat_client, chat_env, session, seeded_superadmin, h_admin)

    await make_templated_member(
        session, seeded_user, email="restricted@acme.com", template_name="Uploader Only",
        permissions=["documents.upload"], workspace_id=str(ws.id),
    )
    h_restricted = await auth(chat_client, "restricted@acme.com")

    r = await chat_client.post(
        f"/api/v1/chats/{chat_id}/messages", json={"content": "hi"}, headers=h_restricted
    )
    assert r.status_code == 403
    assert "requires permission chat.generate" in r.json()["detail"]
