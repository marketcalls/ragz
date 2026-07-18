import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from raghub.modules.auth.models import User


@pytest.fixture
def captured_enqueues(monkeypatch: pytest.MonkeyPatch) -> dict[str, list]:  # type: ignore[type-arg]
    calls: dict[str, list] = {"ingest": [], "delete": []}  # type: ignore[type-arg]
    monkeypatch.setattr("raghub.api.routes.documents.enqueue_ingest",
                        lambda doc_id, size: calls["ingest"].append((doc_id, size)))
    monkeypatch.setattr("raghub.api.routes.documents.enqueue_delete",
                        lambda doc_id, actor_id: calls["delete"].append((doc_id, actor_id)))
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
    from raghub.core.config import get_settings
    monkeypatch.setenv("RAGHUB_MAX_UPLOAD_MB", "0")
    get_settings.cache_clear()
    h = await auth(client, "a@acme.com")
    ws_id = await make_workspace(client, h)
    r = await client.post(
        f"/api/v1/workspaces/{ws_id}/documents", headers=h,
        files={"file": ("big.txt", b"too big for zero", "text/plain")},
    )
    assert r.status_code == 413
    get_settings.cache_clear()
