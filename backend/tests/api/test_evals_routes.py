"""Golden-query admin CRUD routes (Phase 3 §6). Mirrors
tests/api/test_metadata_routes.py's admin-CRUD route shape and
tests/api/test_permissions_routes.py's negative-permission pattern."""

from uuid import UUID

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from ragz.modules.auth.models import User
from ragz.modules.evals.schemas import ComparisonVariantOut
from ragz.modules.models.models import Model
from ragz.modules.outbox import service as outbox_service
from ragz.modules.tenancy.models import RoleTemplate, Workspace, WorkspaceMember


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


@pytest.fixture
def enqueued_evals(monkeypatch):  # type: ignore[no-untyped-def]
    """Records the triggered_by of every evals.run event the route publishes.

    Both trigger tests installed this same publish-spy plus nudge-noop pair
    verbatim; as the outbox contract evolves (it has already gained an event id
    for idempotency) two hand-maintained copies drift apart silently. The spy
    DELEGATES to the real publish rather than replacing it, so the event still
    lands in the caller's transaction and the route under test is unchanged.
    """
    enqueued: list[str] = []
    real_publish = outbox_service.publish

    def _spy_publish(session, *, topic, payload, queue="default"):  # type: ignore[no-untyped-def]
        if topic == "evals.run":
            enqueued.append(payload["triggered_by"])
        return real_publish(session, topic=topic, payload=payload, queue=queue)

    monkeypatch.setattr(outbox_service, "publish", _spy_publish)

    # The route nudges the dispatcher after commit; these tests assert on what
    # was PUBLISHED, so delivery would just drag Celery into an API test.
    async def _noop_dispatch(*_a: object, **_k: object) -> None:
        return None

    monkeypatch.setattr("ragz.api.routes.evals.nudge", _noop_dispatch)
    return enqueued


async def test_trigger_and_list_eval_runs(evals_client, ws_id, h_admin, enqueued_evals) -> None:  # type: ignore[no-untyped-def]
    r = await evals_client.post(f"/api/v1/workspaces/{ws_id}/evals/run", headers=h_admin)
    assert r.status_code == 202 and enqueued_evals == ["manual"]
    r = await evals_client.get(f"/api/v1/workspaces/{ws_id}/evals/runs", headers=h_admin)
    assert r.status_code == 200 and r.json() == []


async def test_eval_run_routes_require_configure_permission(
    evals_client, ws_id, h_engineer,
) -> None:  # type: ignore[no-untyped-def]
    r = await evals_client.post(f"/api/v1/workspaces/{ws_id}/evals/run", headers=h_engineer)
    assert r.status_code == 403
    r = await evals_client.get(f"/api/v1/workspaces/{ws_id}/evals/runs", headers=h_engineer)
    assert r.status_code == 403
    r = await evals_client.post(
        f"/api/v1/workspaces/{ws_id}/evals/compare",
        json={"question": "compare this"},
        headers=h_engineer,
    )
    assert r.status_code == 403


async def test_compare_route_returns_ordered_variants(
    evals_client: httpx.AsyncClient,
    ws_id: str,
    h_admin: dict[str, str],
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = Model(
        litellm_model_name="comparison-model",
        display_name="Comparison model",
        provider_kind="ollama",
        enabled=True,
    )
    session.add(model)
    await session.flush()
    workspace = await session.get(Workspace, UUID(ws_id))
    assert workspace is not None
    workspace.default_model_id = model.id
    await session.commit()

    from ragz.modules.evals import comparison

    captured: dict[str, object] = {}

    async def fake_compare(*args, **kwargs):  # type: ignore[no-untyped-def]
        captured.update(kwargs)
        common = {
            "answer": "Grounded answer [1].",
            "sources": [],
            "citation_markers": [],
            "no_answer": False,
            "retrieval_ms": 4.0,
            "generation_ms": 6.0,
            "total_ms": 10.0,
            "prompt_tokens": 12,
            "completion_tokens": 3,
        }
        return [
            ComparisonVariantOut(mode="single", query_count=1, **common),
            ComparisonVariantOut(mode="multi", query_count=3, **common),
        ]

    monkeypatch.setattr(comparison, "compare_answers", fake_compare)
    # The route resolves a completer before delegating. Supplying one on the
    # app keeps this test provider-free; comparison behavior is covered in the
    # module tests above.
    transport = evals_client._transport  # type: ignore[attr-defined]
    transport.app.state.llm_completer = object()  # type: ignore[attr-defined]

    response = await evals_client.post(
        f"/api/v1/workspaces/{ws_id}/evals/compare",
        json={"question": "Why does the receive window matter?", "model_id": str(model.id)},
        headers=h_admin,
    )

    assert response.status_code == 200
    assert [item["mode"] for item in response.json()["variants"]] == ["single", "multi"]
    assert captured["question"] == "Why does the receive window matter?"
    assert captured["model_id"] == model.id


async def test_compare_route_rejects_embedding_model_id(
    evals_client: httpx.AsyncClient,
    ws_id: str,
    h_admin: dict[str, str],
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    embedding_model = Model(
        litellm_model_name="text-embedding-3-large",
        display_name="Embedding only",
        provider_kind="openai",
        enabled=True,
        modality="embedding",
        dimension=1024,
        collection_name="embedding-only-test",
    )
    session.add(embedding_model)
    await session.commit()
    called = False

    async def fake_compare(*args, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal called
        called = True
        return []

    from ragz.modules.evals import comparison

    monkeypatch.setattr(comparison, "compare_answers", fake_compare)
    transport = evals_client._transport  # type: ignore[attr-defined]
    transport.app.state.llm_completer = object()  # type: ignore[attr-defined]

    response = await evals_client.post(
        f"/api/v1/workspaces/{ws_id}/evals/compare",
        json={"question": "compare this", "model_id": str(embedding_model.id)},
        headers=h_admin,
    )

    assert response.status_code == 404
    assert called is False


async def test_trigger_eval_run_rejects_cross_org_workspace(
    evals_client: httpx.AsyncClient, h_admin: dict[str, str], session: AsyncSession,
    enqueued_evals: list[str],
) -> None:
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

    r = await evals_client.post(f"/api/v1/workspaces/{rival_ws.id}/evals/run", headers=h_admin)
    # get_workspace_checked raises WorkspaceAccessDenied (403) uniformly for
    # cross-org and non-member so existence never leaks (tenancy/service.py's
    # own docstring) -- not a 404. That's the real, established status for
    # every route in this file that already goes through this same check.
    assert r.status_code == 403
    assert enqueued_evals == []
