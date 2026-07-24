"""API tests for the DOC-10 re-embed routes (Task 7):
POST /workspaces/{id}/reembed and GET /workspaces/{id}/reembed-status."""

from uuid import UUID

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from raghub.core.config import get_settings
from raghub.core.db import naive_utc
from raghub.modules.auth.models import User
from raghub.modules.documents import ingest
from raghub.modules.models.models import Model
from raghub.modules.tenancy.models import Workspace
from raghub.modules.tenancy.reembed_models import ReembedJob


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
def captured_reembed(monkeypatch: pytest.MonkeyPatch) -> list[tuple[UUID, UUID, UUID]]:
    calls: list[tuple[UUID, UUID, UUID]] = []
    monkeypatch.setattr(
        "raghub.api.routes.workspaces.enqueue_reembed_workspace",
        lambda workspace_id, job_id, new_embedding_model_id: calls.append(
            (workspace_id, job_id, new_embedding_model_id)
        ),
    )
    return calls


async def test_post_reembed_returns_202_and_enqueues(
    client: httpx.AsyncClient, seeded_user: User, session: AsyncSession,
    captured_reembed: list[tuple[UUID, UUID, UUID]],
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
    # Fix round 2: enqueue_reembed_workspace is now called with the id of a
    # ReembedJob row that already exists (start_reembed created + committed
    # it before enqueueing) -- not a job_id invented after the fact.
    assert captured_reembed == [(UUID(ws_id), UUID(body["id"]), new_model.id)]

    # The race this fix closes: the ReembedJob row must exist SYNCHRONOUSLY,
    # with started_at set, by the time this response has returned -- not
    # only once Celery later picks up the task. Query it directly (bypassing
    # the enqueue mock entirely) to prove that.
    job = await session.get(ReembedJob, UUID(body["id"]))
    assert job is not None
    assert job.started_at is not None
    assert job.finished_at is None


async def test_post_reembed_rejects_non_embedding_model(
    client: httpx.AsyncClient, seeded_user: User, session: AsyncSession,
    captured_reembed: list[tuple[UUID, UUID, UUID]],
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
    captured_reembed: list[tuple[UUID, UUID, UUID]],
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

    # Fix round 2: run_reembed_workspace no longer creates its own
    # ReembedJob row -- it updates one that already exists. Create it here
    # the same way start_reembed now does (synchronously, started_at set)
    # before calling the job body directly (no Celery broker in this test
    # process) -- exercises the SAME code path enqueue_reembed_workspace's
    # Celery task calls, just synchronously.
    ws = await session.get(Workspace, UUID(ws_id))
    assert ws is not None
    job = ReembedJob(
        workspace_id=UUID(ws_id), old_embedding_model_id=ws.embedding_model_id,
        new_embedding_model_id=new_model.id, documents_total=0, started_at=naive_utc(),
    )
    session.add(job)
    await session.commit()

    await ingest.run_reembed_workspace(UUID(ws_id), job.id, new_model.id)

    r = await client.get(f"/api/v1/workspaces/{ws_id}/reembed-status", headers=h)
    assert r.status_code == 200
    body = r.json()
    assert body["workspace_id"] == ws_id
    assert body["new_embedding_model_id"] == str(new_model.id)
    assert body["documents_total"] == 0
    assert body["documents_done"] == 0
    assert body["error"] is None


async def test_upload_immediately_after_reembed_enqueued_is_rejected(
    client: httpx.AsyncClient, seeded_user: User, session: AsyncSession,
    captured_reembed: list[tuple[UUID, UUID, UUID]],
) -> None:
    """Regression for the residual race described in
    .superpowers/sdd/final-review-fix-report.md: previously the ReembedJob
    row only came into existence once Celery actually picked up the task --
    so a document uploaded in the enqueue-to-pickup gap sailed past
    create_from_upload's in-progress guard (no job existed yet) and could
    later be silently wiped by the re-embed's workspace-wide delete.

    This proves that gap is gone: captured_reembed monkeypatches
    enqueue_reembed_workspace to a no-op, so the Celery task NEVER actually
    runs in this test -- there is no async pickup at all. If the guard is
    armed only from the moment start_reembed's route handler commits the
    ReembedJob row (not from whenever a worker eventually gets to it), the
    very next request -- an upload against this workspace -- must already
    be rejected."""
    h = await auth(client, seeded_user.email)
    ws_id = await make_workspace(client, h)
    new_model = await _create_embedding_model(session)

    r = await client.post(
        f"/api/v1/workspaces/{ws_id}/reembed",
        json={"new_embedding_model_id": str(new_model.id)},
        headers=h,
    )
    assert r.status_code == 202
    # Celery never ran (enqueue_reembed_workspace is mocked to a no-op) --
    # the only reason a job could possibly be blocking uploads now is that
    # start_reembed created it synchronously, in its own request.
    assert captured_reembed  # the mock recorded the call; the real task body never executed

    upload = await client.post(
        f"/api/v1/workspaces/{ws_id}/documents", headers=h,
        files={"file": ("racer.txt", b"uploaded in the old enqueue-to-pickup gap", "text/plain")},
    )
    assert upload.status_code == 409
    assert upload.headers["content-type"] == "application/problem+json"
    assert "re-embed job is in progress" in upload.json()["detail"]


async def test_post_reembed_enqueue_failure_closes_job(
    client: httpx.AsyncClient, seeded_user: User, session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fix round 3 regression: start_reembed commits the ReembedJob row
    BEFORE calling enqueue_reembed_workspace. If that call itself raises
    (e.g. the Celery broker/Redis is down), the already-committed job row
    must not be left "in progress" forever -- it must be closed
    (error/finished_at stamped) so create_from_upload's guard doesn't
    permanently reject uploads to this workspace.

    Registering a handler for the bare `Exception` class (as this app does
    for its catch-all 500) makes Starlette's ServerErrorMiddleware build the
    problem+json response AND re-raise the original exception (a documented
    "response of last resort" behavior) -- the `client` fixture's
    ASGITransport (raise_app_exceptions=True, the default) surfaces that as
    the original RuntimeError rather than a captured 500 response, same as
    test_error_handlers.py's crashy_client would need
    raise_app_exceptions=False to observe the response body instead. Either
    way, the job-closing behavior is what this test actually verifies."""
    h = await auth(client, seeded_user.email)
    ws_id = await make_workspace(client, h)
    new_model = await _create_embedding_model(session)

    def _boom(workspace_id: UUID, job_id: UUID, new_embedding_model_id: UUID) -> None:
        raise RuntimeError("celery broker unavailable")

    monkeypatch.setattr("raghub.api.routes.workspaces.enqueue_reembed_workspace", _boom)

    with pytest.raises(RuntimeError, match="celery broker unavailable"):
        await client.post(
            f"/api/v1/workspaces/{ws_id}/reembed",
            json={"new_embedding_model_id": str(new_model.id)},
            headers=h,
        )

    jobs = (
        await session.execute(
            select(ReembedJob).where(ReembedJob.workspace_id == UUID(ws_id))
        )
    ).scalars().all()
    assert len(jobs) == 1
    job = jobs[0]
    assert job.started_at is not None
    assert job.finished_at is not None
    assert job.error is not None and "celery broker unavailable" in job.error
