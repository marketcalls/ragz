"""Tests for the DOC-10 re-embed job (Task 7): run_reembed_workspace moves a
workspace's vectors from its old embedding model's collection into a new
one, using each document's already-stored chunks.json, then deletes the
workspace's points from the OLD collection ONLY after every document has
been successfully re-embedded, and only then flips
workspace.embedding_model_id."""

from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from raghub.core.config import get_settings
from raghub.core.db import naive_utc
from raghub.modules.auth.models import User
from raghub.modules.documents import ingest
from raghub.modules.documents.models import Document
from raghub.modules.documents.service import create_from_upload
from raghub.modules.models import service as models_service
from raghub.modules.models.models import Model
from raghub.modules.retrieval.client import COLLECTION, get_qdrant
from raghub.modules.tenancy import service as tenancy_service
from raghub.modules.tenancy.context import TenantContext
from raghub.modules.tenancy.models import Organization, Workspace
from raghub.modules.tenancy.reembed_models import ReembedJob

TEXT = b"The flux capacitor requires 1.21 gigawatts.\n\nInvoice 0231 covers plutonium."


@pytest.fixture
async def ctx(session: AsyncSession) -> TenantContext:
    """Real Organization + User rows (Workspace.org_id and Document.created_by
    FK to them) -- role="superadmin" so create_from_upload's
    get_workspace_checked call never needs explicit workspace membership,
    mirroring test_embedding_model_lock.py's identically-shaped fixture."""
    org = Organization(name="reembed-org")
    session.add(org)
    await session.flush()
    user = User(
        org_id=org.id, email="reembed@test.com", password_hash="x",  # noqa: S106
        role="superadmin",
    )
    session.add(user)
    await session.flush()
    return TenantContext(
        user_id=user.id, org_id=org.id, role="superadmin",
        workspace_ids=frozenset(), group_ids=frozenset(),
    )


@pytest.fixture
async def embedding_model_fixture(session: AsyncSession, ctx: TenantContext) -> Model:
    """A second embedding model to re-embed INTO. dimension=settings.embedding_dim
    (not an arbitrary 1536 like test_embedding_model_lock.py's fixture) --
    RAGHUB_EMBEDDING_BACKEND=hash always produces settings.embedding_dim-sized
    vectors regardless of which model is "selected" (get_dense_embedder's
    test-only override), so the new collection's declared vector size must
    match that or upsert_points would hit a genuine Qdrant dimension
    mismatch. Mirrors test_retrieve.py's test_retrieve_uses_workspace_specific_
    collection, which hits the exact same constraint."""
    return await models_service.create_model(
        session, ctx, litellm_model_name="new-embedding-model",
        display_name="New Embedding Model", provider_kind="tei", base_url=None,
        api_key=None, settings=get_settings(), modality="embedding",
        dimension=get_settings().embedding_dim,
    )


async def _seed_indexed_document(
    session: AsyncSession, ctx: TenantContext, name: str, data: bytes = TEXT
) -> Document:
    """Real upload + full parse/chunk/embed_upsert run (not a hand-set
    status="indexed" shortcut) -- run_reembed_workspace reads each document's
    chunks.json off storage, so a real ingest run is needed to produce it.
    Called from inside test bodies (never from a fixture) so it always runs
    AFTER the qdrant_collection fixture has set up COLLECTION -- fixture
    instantiation order between two sibling fixtures isn't guaranteed."""
    ws = await tenancy_service.create_workspace(session, ctx, name)
    doc = await create_from_upload(
        session, ctx, ws.id, filename="a.txt", mime="text/plain", data=data
    )
    await ingest.run_parse(doc.id)
    await ingest.run_chunk(doc.id)
    await ingest.run_embed_upsert(doc.id)
    await session.refresh(doc)
    return doc


async def _start_job(
    session: AsyncSession, workspace_id: UUID, old_model_id: UUID, new_model_id: UUID,
) -> ReembedJob:
    """Fix round 2: run_reembed_workspace no longer creates its own
    ReembedJob row -- api/routes/workspaces.py::start_reembed creates it
    synchronously (started_at set) before enqueueing the Celery task, and
    passes the row's id through. Mirror that here so these tests, which
    call run_reembed_workspace directly (no Celery broker in this test
    process), exercise the same shape."""
    job = ReembedJob(
        workspace_id=workspace_id, old_embedding_model_id=old_model_id,
        new_embedding_model_id=new_model_id, documents_total=0, started_at=naive_utc(),
    )
    session.add(job)
    await session.commit()
    return job


async def test_reembed_workspace_moves_vectors_and_flips_model(
    session: AsyncSession, ctx: TenantContext, embedding_model_fixture: Model,
    qdrant_collection: None,
) -> None:
    doc = await _seed_indexed_document(session, ctx, "reembed-ws")
    ws_id = doc.workspace_id
    ws_before = await session.get(Workspace, ws_id)
    assert ws_before is not None
    old_model_id = ws_before.embedding_model_id
    assert old_model_id != embedding_model_fixture.id

    job_row = await _start_job(session, ws_id, old_model_id, embedding_model_fixture.id)
    await ingest.run_reembed_workspace(ws_id, job_row.id, embedding_model_fixture.id)

    # run_reembed_workspace commits via its OWN session (a separate engine
    # connection, per _session()'s docstring) -- session.get()/a plain
    # select() on THIS session would otherwise return the stale
    # identity-mapped object for both ws_before AND job_row (this test's
    # OWN session created job_row via _start_job, so it's already cached),
    # so force a refresh on both instead of re-querying.
    await session.refresh(ws_before)
    ws = ws_before
    assert ws.embedding_model_id == embedding_model_fixture.id

    await session.refresh(job_row)
    job = job_row
    assert job.old_embedding_model_id == old_model_id
    assert job.new_embedding_model_id == embedding_model_fixture.id
    assert job.documents_done == job.documents_total == 1
    assert job.finished_at is not None
    assert job.error is None

    client = get_qdrant()
    new_points, _ = await client.scroll(embedding_model_fixture.collection_name, limit=10)
    assert len(new_points) > 0
    assert all(p.payload["workspace_id"] == str(ws_id) for p in new_points)

    # Old (seeded default) collection must no longer carry this workspace's points.
    old_points, _ = await client.scroll(COLLECTION, limit=10)
    assert not any(p.payload["workspace_id"] == str(ws_id) for p in old_points)


async def test_reembed_isolation_only_touches_target_workspace(
    session: AsyncSession, ctx: TenantContext, embedding_model_fixture: Model,
    qdrant_collection: None,
) -> None:
    """Adversarial: workspace A re-embeds off the shared default collection;
    workspace B (still on the default model, same OLD collection) must keep
    its points -- delete_workspace_points is workspace-scoped, not a wipe."""
    doc_a = await _seed_indexed_document(session, ctx, "reembed-ws-a")
    doc_b = await _seed_indexed_document(session, ctx, "reembed-ws-b")
    ws_a_id, ws_b_id = doc_a.workspace_id, doc_b.workspace_id
    ws_a = await session.get(Workspace, ws_a_id)
    assert ws_a is not None

    job_row = await _start_job(
        session, ws_a_id, ws_a.embedding_model_id, embedding_model_fixture.id
    )
    await ingest.run_reembed_workspace(ws_a_id, job_row.id, embedding_model_fixture.id)

    client = get_qdrant()
    remaining, _ = await client.scroll(COLLECTION, limit=100)
    remaining_workspace_ids = {p.payload["workspace_id"] for p in remaining}
    assert str(ws_b_id) in remaining_workspace_ids
    assert str(ws_a_id) not in remaining_workspace_ids

    ws_b = await session.get(Workspace, ws_b_id)
    assert ws_b is not None
    assert ws_b.embedding_model_id != embedding_model_fixture.id


async def test_reembed_workspace_same_model_is_noop(
    session: AsyncSession, ctx: TenantContext, qdrant_collection: None,
) -> None:
    """Bug fix regression: calling run_reembed_workspace directly with
    new_embedding_model_id == workspace.embedding_model_id (e.g. the Celery
    task invoked directly, bypassing start_reembed's 409 guard) must be a
    no-op -- NOT delete the workspace's existing vectors. Without the guard,
    old_collection == new_collection, so the post-upsert "delete from OLD
    collection" step would wipe every point (including the ones this same
    run just re-upserted) since it targets the very collection the points
    now live in.

    Fix round 2: the ReembedJob row now exists BEFORE run_reembed_workspace
    is ever called (start_reembed creates it synchronously), so this test
    creates one itself and asserts the no-op path still closes it
    (finished_at stamped) -- otherwise create_from_upload's in-progress
    guard would stay armed for this workspace forever."""
    doc = await _seed_indexed_document(session, ctx, "reembed-ws-same-model")
    ws_id = doc.workspace_id
    ws_before = await session.get(Workspace, ws_id)
    assert ws_before is not None
    current_model_id = ws_before.embedding_model_id

    client = get_qdrant()
    before_points, _ = await client.scroll(COLLECTION, limit=10)
    assert any(p.payload["workspace_id"] == str(ws_id) for p in before_points)

    job_row = await _start_job(session, ws_id, current_model_id, current_model_id)
    await ingest.run_reembed_workspace(ws_id, job_row.id, current_model_id)

    await session.refresh(ws_before)
    assert ws_before.embedding_model_id == current_model_id  # unchanged

    after_points, _ = await client.scroll(COLLECTION, limit=10)
    assert any(p.payload["workspace_id"] == str(ws_id) for p in after_points)

    # The job must be closed (not left "in progress" forever) even on this
    # defensive no-op path -- otherwise the create_from_upload guard would
    # permanently block uploads to this workspace. Refresh (not a plain
    # select) since this test's own session already cached job_row via
    # _start_job -- see the success test's comment on why that matters.
    await session.refresh(job_row)
    assert job_row.finished_at is not None


async def test_reembed_failure_leaves_workspace_on_old_model(
    session: AsyncSession, ctx: TenantContext, embedding_model_fixture: Model,
    qdrant_collection: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A mid-loop embed/upsert failure must record ReembedJob.error, leave
    workspace.embedding_model_id UNCHANGED, and never touch the old
    collection -- the workspace must never end up pointing at a model whose
    collection wasn't actually fully populated."""
    doc = await _seed_indexed_document(session, ctx, "reembed-ws-fail")
    ws_id = doc.workspace_id
    ws_before = await session.get(Workspace, ws_id)
    assert ws_before is not None
    old_model_id = ws_before.embedding_model_id

    async def _boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("embed batch exploded")

    monkeypatch.setattr(ingest, "embed_batch", _boom)

    job_row = await _start_job(session, ws_id, old_model_id, embedding_model_fixture.id)
    with pytest.raises(RuntimeError, match="embed batch exploded"):
        await ingest.run_reembed_workspace(ws_id, job_row.id, embedding_model_fixture.id)

    # Force a refresh past this session's identity map (see the success
    # test's comment) -- not load-bearing here since the value shouldn't
    # have changed either way, but keeps this test honest about what it's
    # actually reading.
    await session.refresh(ws_before)
    assert ws_before.embedding_model_id == old_model_id  # unchanged

    # Refresh (not a plain select) since this test's own session already
    # cached job_row via _start_job -- see the success test's comment.
    await session.refresh(job_row)
    assert job_row.error is not None and "embed batch exploded" in job_row.error
    assert job_row.finished_at is not None
    assert job_row.documents_done == 0

    # Old collection must still carry this workspace's points -- the failed
    # run must never have reached the old-collection delete.
    client = get_qdrant()
    old_points, _ = await client.scroll(COLLECTION, limit=10)
    assert any(p.payload["workspace_id"] == str(ws_id) for p in old_points)
