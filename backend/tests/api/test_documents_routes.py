from uuid import UUID

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from ragz.modules.auth.models import User
from ragz.modules.outbox import service as outbox_service
from ragz.modules.tenancy.models import WorkspaceMember
from tests.api.test_permissions_routes import make_templated_member


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
        return real_publish(session, topic=topic, payload=payload, queue=queue)

    monkeypatch.setattr(outbox_service, "publish", _spy_publish)
    # The nudge is pure latency optimisation; the event is already durable.
    async def _noop_dispatch(*_a: object, **_k: object) -> int:
        return 0

    monkeypatch.setattr("ragz.api.routes.documents.dispatch_pending", _noop_dispatch)
    monkeypatch.setattr("ragz.api.routes.documents.enqueue_delete",
                        lambda doc_id, actor_id: calls["delete"].append((doc_id, actor_id)))
    monkeypatch.setattr("ragz.api.routes.documents.enqueue_reindex",
                        lambda doc_id: calls["reindex"].append(doc_id))
    return calls


async def auth(client: httpx.AsyncClient, email: str) -> dict[str, str]:
    r = await client.post("/api/v1/auth/login", json={"email": email, "password": "pw123456"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def make_workspace(client: httpx.AsyncClient, h: dict[str, str]) -> str:
    r = await client.post("/api/v1/workspaces", json={"name": "Docs"}, headers=h)
    return str(r.json()["id"])


async def test_upload_list_delete_flow(
    client: httpx.AsyncClient, seeded_user: User, stack_env: None,
    captured_enqueues: dict,  # type: ignore[type-arg]
) -> None:
    h = await auth(client, "a@acme.com")
    ws_id = await make_workspace(client, h)

    r = await client.post(
        f"/api/v1/workspaces/{ws_id}/documents", headers=h,
        files={"file": ("notes.txt", b"the flux capacitor hums", "text/plain")},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["status"] == "queued" and body["filename"] == "notes.txt"
    assert len(captured_enqueues["ingest"]) == 1

    listing = await client.get(f"/api/v1/workspaces/{ws_id}/documents", headers=h)
    assert [d["id"] for d in listing.json()] == [body["id"]]

    # duplicate content in the same workspace -> 409
    r2 = await client.post(
        f"/api/v1/workspaces/{ws_id}/documents", headers=h,
        files={"file": ("copy.txt", b"the flux capacitor hums", "text/plain")},
    )
    assert r2.status_code == 409

    r3 = await client.delete(f"/api/v1/documents/{body['id']}", headers=h)
    assert r3.status_code == 202
    assert len(captured_enqueues["delete"]) == 1

    # Status flips to "deleting" before the (mocked, never-run) worker task
    # even gets a chance to run — a crashed/lost delete task must never leave
    # the document looking untouched.
    listing_after_delete = await client.get(f"/api/v1/workspaces/{ws_id}/documents", headers=h)
    deleted_doc = next(d for d in listing_after_delete.json() if d["id"] == body["id"])
    assert deleted_doc["status"] == "deleting"


async def test_non_member_user_gets_403(
    client: httpx.AsyncClient, seeded_user: User, session: AsyncSession, stack_env: None,
    captured_enqueues: dict,  # type: ignore[type-arg]
) -> None:
    plain = User(org_id=seeded_user.org_id, email="p@acme.com",
                 password_hash=seeded_user.password_hash, role="user")
    session.add(plain)
    await session.commit()
    h_admin = await auth(client, "a@acme.com")
    ws_id = await make_workspace(client, h_admin)
    h_user = await auth(client, "p@acme.com")
    r = await client.post(
        f"/api/v1/workspaces/{ws_id}/documents", headers=h_user,
        files={"file": ("a.txt", b"x", "text/plain")},
    )
    assert r.status_code == 403
    assert (await client.get(f"/api/v1/workspaces/{ws_id}/documents",
                             headers=h_user)).status_code == 403


async def test_list_documents_requires_documents_list_permission(
    client: httpx.AsyncClient, seeded_user: User, session: AsyncSession,
) -> None:
    # A custom role that carries NEITHER documents.list NOR search.execute --
    # only an unrelated, harmless permission (chat.read) -- must be denied
    # the workspace document listing (RBAC-03: documents.list is now an
    # explicit gate, not implicit for any authenticated member).
    h_admin = await auth(client, "a@acme.com")
    ws_id = await make_workspace(client, h_admin)
    await make_templated_member(
        session, seeded_user, email="narrow@acme.com", template_name="NoListOrSearch",
        permissions=["chat.read"], workspace_id=ws_id,
    )
    h_narrow = await auth(client, "narrow@acme.com")

    r = await client.get(f"/api/v1/workspaces/{ws_id}/documents", headers=h_narrow)
    assert r.status_code == 403


async def test_default_member_can_still_list_documents(
    client: httpx.AsyncClient, seeded_user: User, session: AsyncSession,
) -> None:
    # A plain role="user" account with NO custom_role_id falls back to
    # DEFAULT_USER_PERMISSIONS, which still includes documents.list -- the new
    # require_action("documents.list") gate must not regress that
    # non-destructive read floor. (The sibling positive case for search.execute
    # is test_search_route.py::test_default_member_can_search.)
    h_admin = await auth(client, "a@acme.com")
    ws_id = await make_workspace(client, h_admin)
    contributor = User(org_id=seeded_user.org_id, email="contributor@acme.com",
                       password_hash=seeded_user.password_hash, role="user")
    session.add(contributor)
    await session.flush()
    session.add(WorkspaceMember(workspace_id=UUID(ws_id), user_id=contributor.id))
    await session.commit()
    h_contributor = await auth(client, "contributor@acme.com")

    r = await client.get(f"/api/v1/workspaces/{ws_id}/documents", headers=h_contributor)
    assert r.status_code == 200


async def test_delete_unknown_document_404(
    client: httpx.AsyncClient, seeded_user: User, stack_env: None,
    captured_enqueues: dict,  # type: ignore[type-arg]
) -> None:
    h = await auth(client, "a@acme.com")
    r = await client.delete("/api/v1/documents/00000000-0000-0000-0000-000000000000",
                            headers=h)
    assert r.status_code == 404
    assert captured_enqueues["delete"] == []


async def test_oversized_upload_413(
    client: httpx.AsyncClient, seeded_user: User, stack_env: None,
    monkeypatch: pytest.MonkeyPatch,
    captured_enqueues: dict,  # type: ignore[type-arg]
) -> None:
    from ragz.core.config import get_settings
    monkeypatch.setenv("RAGZ_MAX_UPLOAD_MB", "0")
    get_settings.cache_clear()
    h = await auth(client, "a@acme.com")
    ws_id = await make_workspace(client, h)
    r = await client.post(
        f"/api/v1/workspaces/{ws_id}/documents", headers=h,
        files={"file": ("big.txt", b"too big for zero", "text/plain")},
    )
    assert r.status_code == 413
    get_settings.cache_clear()


async def test_oversized_upload_chunked_abort_413(
    client: httpx.AsyncClient, seeded_user: User, stack_env: None,
    monkeypatch: pytest.MonkeyPatch,
    captured_enqueues: dict,  # type: ignore[type-arg]
) -> None:
    """Test that oversized uploads are aborted during chunked read."""
    from ragz.core.config import get_settings
    monkeypatch.setenv("RAGZ_MAX_UPLOAD_MB", "1")
    get_settings.cache_clear()
    h = await auth(client, "a@acme.com")
    ws_id = await make_workspace(client, h)
    # Create a 2MB file payload (exceeds 1MB limit)
    large_data = b"x" * (2 * 1024 * 1024)
    r = await client.post(
        f"/api/v1/workspaces/{ws_id}/documents", headers=h,
        files={"file": ("large.bin", large_data, "application/octet-stream")},
    )
    assert r.status_code == 413
    assert captured_enqueues["ingest"] == []
    get_settings.cache_clear()


async def test_approve_route_enqueues_reindex_when_needed(
    client: httpx.AsyncClient, seeded_user: User, session: AsyncSession,
    stack_env: None, qdrant_collection: None,
    captured_enqueues: dict,  # type: ignore[type-arg]
) -> None:
    """Plan K Task 11: the approve route (api/routes/documents.py) is the
    ENTRYPOINT that performs the enqueue -- service.set_approved only returns
    (doc, needs_reindex). Drives the real ingestion pipeline directly (same
    pattern as tests/api/test_search_route.py) to build the
    v1-demoted-then-reapproved scenario, then hits the HTTP approve route and
    asserts enqueue_reindex fires with v1's id."""
    from sqlalchemy import select

    from ragz.core.app_settings import set_app_setting
    from ragz.modules.documents.ingest import run_chunk, run_embed_upsert, run_parse
    from ragz.modules.documents.service import create_from_upload
    from ragz.modules.tenancy.context import TenantContext
    from ragz.modules.tenancy.models import Workspace

    # Plain .txt fixture content -- anydoc (the install-wide default) does not
    # parse .txt at all; this test is about the approve-route reindex
    # enqueue, not parser choice, so pin docling explicitly.
    await set_app_setting(session, "document_parser", "docling")
    h = await auth(client, "a@acme.com")
    await make_workspace(client, h)  # created via HTTP so the route exercises real membership
    ws = (await session.execute(select(Workspace))).scalar_one()
    ctx = TenantContext(user_id=seeded_user.id, org_id=seeded_user.org_id,
                        role="admin", workspace_ids=frozenset())

    async def _index(filename: str, text: str):  # type: ignore[no-untyped-def]
        doc = await create_from_upload(
            session, ctx, ws.id, filename=filename, mime="text/plain", data=text.encode()
        )
        await run_parse(doc.id)
        await run_chunk(doc.id)
        await run_embed_upsert(doc.id)
        await session.refresh(doc)
        return doc

    v1 = await _index("policy.txt", "the muster point is DOCK 4")
    await _index("policy.txt", "the muster point is GATE 9")  # demotes v1, deletes its points
    await session.refresh(v1)
    assert v1.vectors_present is False

    r = await client.put(
        f"/api/v1/documents/{v1.id}/approved", headers=h, json={"approved": True},
    )
    assert r.status_code == 200
    assert captured_enqueues["reindex"] == [v1.id]


async def test_approve_route_no_reindex_when_points_already_present(
    client: httpx.AsyncClient, seeded_user: User, session: AsyncSession,
    stack_env: None, qdrant_collection: None,
    captured_enqueues: dict,  # type: ignore[type-arg]
) -> None:
    """Companion to the above: approving a version that already has live
    points is a pure flip -- no reindex needed, route must not enqueue."""
    from sqlalchemy import select

    from ragz.core.app_settings import set_app_setting
    from ragz.modules.documents.ingest import run_chunk, run_embed_upsert, run_parse
    from ragz.modules.documents.service import create_from_upload
    from ragz.modules.tenancy.context import TenantContext
    from ragz.modules.tenancy.models import Workspace

    # Plain .txt fixture content -- anydoc (the install-wide default) does not
    # parse .txt at all; this test is about the approve-route reindex
    # enqueue, not parser choice, so pin docling explicitly.
    await set_app_setting(session, "document_parser", "docling")
    h = await auth(client, "a@acme.com")
    await make_workspace(client, h)  # created via HTTP so the route exercises real membership
    ws = (await session.execute(select(Workspace))).scalar_one()
    ctx = TenantContext(user_id=seeded_user.id, org_id=seeded_user.org_id,
                        role="admin", workspace_ids=frozenset())

    doc = await create_from_upload(
        session, ctx, ws.id, filename="policy.txt",
        mime="text/plain", data=b"the muster point is DOCK 4",
    )
    await run_parse(doc.id)
    await run_chunk(doc.id)
    await run_embed_upsert(doc.id)

    r = await client.put(
        f"/api/v1/documents/{doc.id}/approved", headers=h, json={"approved": True},
    )
    assert r.status_code == 200
    assert captured_enqueues["reindex"] == []
