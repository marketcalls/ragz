"""Plan J Task 12 (§6): the retrieval-settings-change eval trigger inside
update_retrieval_settings. Local ctx/ws fixtures mirror
tests/modules/evals/conftest.py's seed_workspace pattern (role="admin" so the
fixture ctx can call update_retrieval_settings without a WorkspacePatch/route
in the way)."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from raghub.core.config import get_settings
from raghub.modules.evals import service as evals_service
from raghub.modules.models.models import LOCAL_EMBEDDING_MODEL_ID, Model
from raghub.modules.tenancy import service
from raghub.modules.tenancy.context import TenantContext
from raghub.modules.tenancy.models import Workspace
from tests.modules.retrieval.test_retrieve import seed_workspace


@pytest.fixture
async def seeded_local_model(session: AsyncSession) -> Model:
    """DOC-10 Task 5: seed_workspace below creates a Workspace directly, whose
    embedding_model_id FK now requires this row to exist first -- this suite's
    plain `session` fixture builds schema via `Base.metadata.create_all`, not
    the real alembic chain, so migration d1e8f4a2b6c3's seed INSERT never runs
    here. Mirrors tests/modules/models/test_embedding_models.py's identically
    named fixture."""
    model = Model(
        id=LOCAL_EMBEDDING_MODEL_ID,
        litellm_model_name="local-embeddings",
        display_name="Local Embeddings (bge-m3)",
        provider_kind="tei",
        enabled=True,
        sync_status="synced",
        modality="embedding",
        dimension=get_settings().embedding_dim,
        collection_name="chunks_bge_m3",
    )
    session.add(model)
    await session.commit()
    return model


@pytest.fixture
async def ctx_ws(
    session: AsyncSession, stack_env: None, seeded_local_model: Model
) -> tuple[TenantContext, Workspace]:
    return await seed_workspace(session, "tenancy-settings", role="admin")


@pytest.fixture
async def ctx(ctx_ws: tuple[TenantContext, Workspace]) -> TenantContext:
    return ctx_ws[0]


@pytest.fixture
async def ws(ctx_ws: tuple[TenantContext, Workspace]) -> Workspace:
    return ctx_ws[1]


async def test_top_k_change_triggers_eval_run_when_golden_queries_exist(
    session: AsyncSession, ctx: TenantContext, ws: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    await evals_service.create_golden_query(
        session, ctx, ws.id, question="q", expected_document_ids=[]
    )
    enqueued: list[tuple[object, str]] = []
    # enqueue_eval_run is imported locally inside update_retrieval_settings
    # (avoids a real circular import -- evals.service already imports
    # tenancy.service at module scope), so the interception point is the
    # worker.tasks module attribute itself, not a tenancy.service name --
    # mirrors tests/modules/documents/test_versioning.py's enqueue_reindex
    # patching for the same reason.
    monkeypatch.setattr(
        "raghub.worker.tasks.enqueue_eval_run",
        lambda workspace_id, triggered_by: enqueued.append((workspace_id, triggered_by)),
    )
    await service.update_retrieval_settings(session, ctx, ws.id, {"top_k": 12})
    assert enqueued == [(ws.id, "settings_change")]


async def test_fallback_policy_change_does_not_trigger_eval_run(
    session: AsyncSession, ctx: TenantContext, ws: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """fallback_policy doesn't affect retrieval ranking -- only
    top_k/min_score/rerank_enabled do."""
    enqueued: list[tuple[object, str]] = []
    monkeypatch.setattr(
        "raghub.worker.tasks.enqueue_eval_run",
        lambda workspace_id, triggered_by: enqueued.append((workspace_id, triggered_by)),
    )
    await service.update_retrieval_settings(session, ctx, ws.id, {"fallback_policy": "decline"})
    assert enqueued == []
