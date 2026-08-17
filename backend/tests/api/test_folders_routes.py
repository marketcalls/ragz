from uuid import UUID

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from ragz.modules.auth.models import User
from ragz.modules.outbox import service as outbox_service


@pytest.fixture
def captured_enqueues(monkeypatch: pytest.MonkeyPatch) -> dict[str, list]:  # type: ignore[type-arg]
    """Mirrors tests/api/test_documents_routes.py's identically-shaped
    fixture (not shared via conftest, so redefined here) -- delete_folder's
    route loops calling enqueue_delete once per document, same as the
    single-document delete route.

    Ingest now goes through the transactional outbox (review P1), so the spy sits
    on publish rather than on a direct enqueue call."""
    calls: dict[str, list] = {"ingest": [], "delete": [], "reindex": []}  # type: ignore[type-arg]
    real_publish = outbox_service.publish

    def _spy_publish(session, *, topic, payload, queue="default"):  # type: ignore[no-untyped-def]
        if topic == "documents.ingest":
            calls["ingest"].append((UUID(payload["document_id"]), payload["size_bytes"]))
        return real_publish(session, topic=topic, payload=payload, queue=queue)

    monkeypatch.setattr(outbox_service, "publish", _spy_publish)

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


async def test_delete_preview_reports_document_and_subfolder_counts_before_deleting(
    client: httpx.AsyncClient, seeded_user: User, session: AsyncSession, stack_env: None,
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
    parent_id = r.json()["id"]
    r = await client.post(
        f"/api/v1/workspaces/{ws_id}/folders",
        json={"name": "Contracts", "parent_folder_id": parent_id},
        headers=h_admin,
    )
    child_id = r.json()["id"]

    r = await client.post(
        f"/api/v1/workspaces/{ws_id}/documents", headers=h_admin,
        files={"file": ("a.txt", b"in parent", "text/plain")},
        data={"folder_id": parent_id},
    )
    assert r.status_code == 201
    r = await client.post(
        f"/api/v1/workspaces/{ws_id}/documents", headers=h_admin,
        files={"file": ("b.txt", b"in child", "text/plain")},
        data={"folder_id": child_id},
    )
    assert r.status_code == 201

    r = await client.get(f"/api/v1/folders/{parent_id}/delete-preview", headers=h_admin)
    assert r.status_code == 200
    assert r.json() == {"document_count": 2, "subfolder_count": 1}

    # The preview must not have deleted anything -- both folders still list.
    r = await client.get(f"/api/v1/workspaces/{ws_id}/folders", headers=h_admin)
    assert {f["name"] for f in r.json()} == {"Legal", "Contracts"}


async def test_delete_folder_cascades_and_enqueues_each_document(
    client: httpx.AsyncClient, seeded_user: User, session: AsyncSession,
    stack_env: None, captured_enqueues: dict,  # type: ignore[type-arg]
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
    parent_id = r.json()["id"]
    r = await client.post(
        f"/api/v1/workspaces/{ws_id}/folders",
        json={"name": "Contracts", "parent_folder_id": parent_id},
        headers=h_admin,
    )
    child_id = r.json()["id"]

    r = await client.post(
        f"/api/v1/workspaces/{ws_id}/documents", headers=h_admin,
        files={"file": ("a.txt", b"in parent", "text/plain")},
        data={"folder_id": parent_id},
    )
    assert r.status_code == 201
    doc_in_parent_id = r.json()["id"]

    r = await client.post(
        f"/api/v1/workspaces/{ws_id}/documents", headers=h_admin,
        files={"file": ("b.txt", b"in child", "text/plain")},
        data={"folder_id": child_id},
    )
    assert r.status_code == 201
    doc_in_child_id = r.json()["id"]

    r = await client.delete(f"/api/v1/folders/{parent_id}", headers=h_admin)
    assert r.status_code == 202
    assert r.json() == {"documents_deleted": 2}
    assert {str(doc_id) for doc_id, _actor in captured_enqueues["delete"]} == {
        doc_in_parent_id, doc_in_child_id,
    }

    r = await client.get(f"/api/v1/workspaces/{ws_id}/folders", headers=h_admin)
    assert r.json() == []
