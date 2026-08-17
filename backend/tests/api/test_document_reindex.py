"""Per-document Reindex route (POST /documents/{id}/reindex).

Mirrors the approve/delete route tests: a permitted user reindexes an
accessible document (202 + enqueue_reindex fired), a restricted document a
member can SEE but not open is denied non-leaking, a role without the WRITE
action gets 403, and an unknown id is 404 with nothing enqueued. The route
reuses the "documents.upload" catalog action.
"""
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from ragz.modules.auth.models import User
from ragz.modules.documents.models import Document
from ragz.modules.outbox import service as outbox_service
from ragz.modules.tenancy.models import Group
from tests.api.test_permissions_routes import make_templated_member


@pytest.fixture
def captured_reindex(monkeypatch: pytest.MonkeyPatch) -> list[UUID]:
    calls: list[UUID] = []
    real_publish = outbox_service.publish

    def _spy_publish(session, *, topic, payload, queue="default"):  # type: ignore[no-untyped-def]
        if topic == "documents.reindex":
            calls.append(UUID(payload["document_id"]))
        return real_publish(session, topic=topic, payload=payload, queue=queue)

    monkeypatch.setattr(outbox_service, "publish", _spy_publish)

    async def _noop_dispatch(*_a: object, **_k: object) -> int:
        return 0

    monkeypatch.setattr("ragz.api.routes.documents.dispatch_pending", _noop_dispatch)
    return calls


async def auth(client: httpx.AsyncClient, email: str) -> dict[str, str]:
    r = await client.post("/api/v1/auth/login", json={"email": email, "password": "pw123456"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def test_admin_reindex_indexed_document_enqueues(
    client: httpx.AsyncClient, seeded_user: User, chat_env: dict, session: AsyncSession,
    captured_reindex: list[UUID],
) -> None:
    """Happy path: an admin (holds documents.upload implicitly) reindexes an
    indexed document -> 202 {"status": "reindexing"} and enqueue_reindex fires
    with the document's id."""
    doc: Document = chat_env["document"]  # status="indexed"
    h = await auth(client, "a@acme.com")
    r = await client.post(f"/api/v1/documents/{doc.id}/reindex", headers=h)
    assert r.status_code == 202
    assert r.json() == {"status": "reindexing"}
    assert captured_reindex == [doc.id]


async def test_reindex_unknown_document_404(
    client: httpx.AsyncClient, seeded_user: User, chat_env: dict,
    captured_reindex: list[UUID],
) -> None:
    h = await auth(client, "a@acme.com")
    r = await client.post(
        f"/api/v1/documents/{uuid4()}/reindex", headers=h
    )
    assert r.status_code == 404
    assert captured_reindex == []


async def test_reindex_non_reindexable_state_409(
    client: httpx.AsyncClient, seeded_user: User, chat_env: dict, session: AsyncSession,
    captured_reindex: list[UUID],
) -> None:
    """A document still mid-ingest (queued/processing) is not reindexable ->
    409, nothing enqueued."""
    doc: Document = chat_env["document"]
    doc.status = "processing"
    await session.commit()
    h = await auth(client, "a@acme.com")
    r = await client.post(f"/api/v1/documents/{doc.id}/reindex", headers=h)
    assert r.status_code == 409
    assert captured_reindex == []


async def test_reindex_denied_without_upload_action_403(
    client: httpx.AsyncClient, seeded_user: User, chat_env: dict, session: AsyncSession,
    captured_reindex: list[UUID],
) -> None:
    """A custom role that can SEE the workspace/document (read floor) but does
    NOT hold documents.upload is denied at the require_action boundary (403),
    with nothing enqueued."""
    ws = chat_env["workspace"]
    doc: Document = chat_env["document"]
    await make_templated_member(
        session, seeded_user, email="reader@acme.com", template_name="ReadOnlyNoUpload",
        permissions=["workspace.read", "documents.list", "documents.content.read"],
        workspace_id=str(ws.id),
    )
    h = await auth(client, "reader@acme.com")
    r = await client.post(f"/api/v1/documents/{doc.id}/reindex", headers=h)
    assert r.status_code == 403
    assert captured_reindex == []


async def test_reindex_restricted_document_denied_non_leaking(
    client: httpx.AsyncClient, seeded_user: User, chat_env: dict, session: AsyncSession,
    captured_reindex: list[UUID],
) -> None:
    """The ACL test: a member who HOLDS documents.upload and can SEE the
    restricted document in listings (Drive-style existence) but is NOT in its
    ACL group must be denied -- reindex acts on content, so the content ACL is
    re-checked after get_document_checked. Nothing enqueued."""
    ws = chat_env["workspace"]
    doc: Document = chat_env["document"]
    group = Group(org_id=seeded_user.org_id, name="finance")
    session.add(group)
    await session.flush()
    doc.acl_group_ids = [group.id]  # restrict to a group the member is not in
    await session.commit()

    # A contributor-shaped member: has the WRITE action so the 403 here is
    # decided by the document ACL, not the require_action boundary.
    await make_templated_member(
        session, seeded_user, email="uploader@acme.com", template_name="UploaderNoGroup",
        permissions=[
            "workspace.read", "documents.list", "documents.content.read", "documents.upload",
        ],
        workspace_id=str(ws.id),
    )
    h = await auth(client, "uploader@acme.com")
    r = await client.post(f"/api/v1/documents/{doc.id}/reindex", headers=h)
    assert r.status_code in (403, 404)
    assert captured_reindex == []


async def test_reindex_route_policy_and_enforcement_green(
    client: httpx.AsyncClient,
) -> None:
    """The new route must be cataloged AND actually enforce its declared
    action -- the two CI gates that guard "no unclassified/unenforced route
    reaches main"."""
    from ragz.api.app import create_app
    from ragz.api.policy import audit_route_policy, audit_unmodeled_enforcement_gaps

    app = create_app()
    assert audit_route_policy(app) == []
    assert audit_unmodeled_enforcement_gaps(app) == []
