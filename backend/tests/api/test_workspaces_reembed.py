"""API tests for the DOC-10 re-embed routes (Task 7):
POST /workspaces/{id}/reembed and GET /workspaces/{id}/reembed-status."""

from uuid import UUID

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from raghub.core.config import get_settings
from raghub.modules.auth.models import User
from raghub.modules.documents import ingest
from raghub.modules.models.models import Model


async def auth(client: httpx.AsyncClient, email: str) -> dict[str, str]:
    r = await client.post("/api/v1/auth/login", json={"email": email, "password": "pw123456"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def make_workspace(client: httpx.AsyncClient, h: dict[str, str]) -> str:
    r = await client.post("/api/v1/workspaces", json={"name": "Reembed"}, headers=h)
    assert r.status_code == 201
    return str(r.json()["id"])


async def _create_embedding_model(session: AsyncSession, name: str = "new-embed") -> Model:
    """Same two-step shape as models_service.create_model: flush for the id,
    then stamp collection_name (needs the id) before commit."""
    model = Model(
        litellm_model_name=name, display_name=name, provider_kind="tei",
        modality="embedding", dimension=get_settings().embedding_dim,
    )
    session.add(model)
    await session.flush()
    model.collection_name = f"chunks_{model.id.hex}"
    await session.commit()
    return model


async def _create_chat_model(session: AsyncSession, name: str = "chat-model") -> Model:
    model = Model(litellm_model_name=name, display_name=name, provider_kind="openai")
    session.add(model)
    await session.commit()
    return model


@pytest.fixture
def captured_reembed(monkeypatch: pytest.MonkeyPatch) -> list[tuple[UUID, UUID]]:
    calls: list[tuple[UUID, UUID]] = []
    monkeypatch.setattr(
        "raghub.api.routes.workspaces.enqueue_reembed_workspace",
        lambda workspace_id, new_embedding_model_id: calls.append(
            (workspace_id, new_embedding_model_id)
        ),
    )
    return calls


async def test_post_reembed_returns_202_and_enqueues(
    client: httpx.AsyncClient, seeded_user: User, session: AsyncSession,
    captured_reembed: list[tuple[UUID, UUID]],
) -> None:
    h = await auth(client, seeded_user.email)
    ws_id = await make_workspace(client, h)
    new_model = await _create_embedding_model(session)

    r = await client.post(
        f"/api/v1/workspaces/{ws_id}/reembed",
        json={"new_embedding_model_id": str(new_model.id)},
        headers=h,
    )
    assert r.status_code == 202
    body = r.json()
    assert body["workspace_id"] == ws_id
    assert body["new_embedding_model_id"] == str(new_model.id)
    assert body["documents_total"] == 0
    assert body["documents_done"] == 0
    assert body["error"] is None
    assert body["finished_at"] is None
    assert captured_reembed == [(UUID(ws_id), new_model.id)]


async def test_post_reembed_rejects_non_embedding_model(
    client: httpx.AsyncClient, seeded_user: User, session: AsyncSession,
    captured_reembed: list[tuple[UUID, UUID]],
) -> None:
    h = await auth(client, seeded_user.email)
    ws_id = await make_workspace(client, h)
    chat_model = await _create_chat_model(session)

    r = await client.post(
        f"/api/v1/workspaces/{ws_id}/reembed",
        json={"new_embedding_model_id": str(chat_model.id)},
        headers=h,
    )
    assert r.status_code == 409
    assert r.headers["content-type"] == "application/problem+json"
    assert captured_reembed == []


async def test_post_reembed_rejects_same_model(
    client: httpx.AsyncClient, seeded_user: User, session: AsyncSession,
    captured_reembed: list[tuple[UUID, UUID]],
) -> None:
    """Bug fix regression: requesting re-embed into the workspace's CURRENT
    embedding model (e.g. a client double-submit/retry after a previous
    reembed already flipped the model) must be rejected with 409 and must
    NEVER reach enqueue_reembed_workspace -- old_collection == new_collection
    would otherwise make run_reembed_workspace's post-upsert delete-from-OLD
    step wipe every point it just wrote."""
    h = await auth(client, seeded_user.email)
    ws_id = await make_workspace(client, h)

    ws_list = await client.get("/api/v1/workspaces", headers=h)
    current_model_id = next(
        w["embedding_model_id"] for w in ws_list.json() if w["id"] == ws_id
    )

    r = await client.post(
        f"/api/v1/workspaces/{ws_id}/reembed",
        json={"new_embedding_model_id": current_model_id},
        headers=h,
    )
    assert r.status_code == 409
    assert r.headers["content-type"] == "application/problem+json"
    assert captured_reembed == []


async def test_reembed_status_404_before_any_job(
    client: httpx.AsyncClient, seeded_user: User
) -> None:
    h = await auth(client, seeded_user.email)
    ws_id = await make_workspace(client, h)

    r = await client.get(f"/api/v1/workspaces/{ws_id}/reembed-status", headers=h)
    assert r.status_code == 404
    assert r.headers["content-type"] == "application/problem+json"


async def test_reembed_status_200_after_completion(
    client: httpx.AsyncClient, seeded_user: User, session: AsyncSession,
    qdrant_collection: None,
) -> None:
    h = await auth(client, seeded_user.email)
    ws_id = await make_workspace(client, h)
    new_model = await _create_embedding_model(session, name="new-embed-2")

    # Run the job body directly (no Celery broker in this test process) --
    # exercises the SAME code path enqueue_reembed_workspace's Celery task
    # calls, just synchronously.
    await ingest.run_reembed_workspace(UUID(ws_id), new_model.id)

    r = await client.get(f"/api/v1/workspaces/{ws_id}/reembed-status", headers=h)
    assert r.status_code == 200
    body = r.json()
    assert body["workspace_id"] == ws_id
    assert body["new_embedding_model_id"] == str(new_model.id)
    assert body["documents_total"] == 0
    assert body["documents_done"] == 0
    assert body["error"] is None
    assert body["finished_at"] is not None
