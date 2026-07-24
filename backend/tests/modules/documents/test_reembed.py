"""Tests for the DOC-10 re-embed job (Task 7): run_reembed_workspace moves a
workspace's vectors from its old embedding model's collection into a new
one, using each document's already-stored chunks.json, then deletes the
workspace's points from the OLD collection ONLY after every document has
been successfully re-embedded, and only then flips
workspace.embedding_model_id."""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from raghub.core.config import get_settings
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

    await ingest.run_reembed_workspace(ws_id, embedding_model_fixture.id)

    # run_reembed_workspace commits via its OWN session (a separate engine
    # connection, per _session()'s docstring) -- session.get() on THIS
    # session would otherwise return the stale identity-mapped object from
    # ws_before above instead of re-querying, so force a refresh.
    await session.refresh(ws_before)
    ws = ws_before
    assert ws.embedding_model_id == embedding_model_fixture.id

    job = (
        await session.execute(select(ReembedJob).where(ReembedJob.workspace_id == ws_id))
    ).scalar_one()
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

    await ingest.run_reembed_workspace(ws_a_id, embedding_model_fixture.id)

    client = get_qdrant()
    remaining, _ = await client.scroll(COLLECTION, limit=100)
    remaining_workspace_ids = {p.payload["workspace_id"] for p in remaining}
    assert str(ws_b_id) in remaining_workspace_ids
    assert str(ws_a_id) not in remaining_workspace_ids

    ws_b = await session.get(Workspace, ws_b_id)
    assert ws_b is not None
    assert ws_b.embedding_model_id != embedding_model_fixture.id


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

    with pytest.raises(RuntimeError, match="embed batch exploded"):
        await ingest.run_reembed_workspace(ws_id, embedding_model_fixture.id)

    # Force a refresh past this session's identity map (see the success
    # test's comment) -- not load-bearing here since the value shouldn't
    # have changed either way, but keeps this test honest about what it's
    # actually reading.
    await session.refresh(ws_before)
    assert ws_before.embedding_model_id == old_model_id  # unchanged

    job = (
        await session.execute(select(ReembedJob).where(ReembedJob.workspace_id == ws_id))
    ).scalar_one()
    assert job.error is not None and "embed batch exploded" in job.error
    assert job.finished_at is not None
    assert job.documents_done == 0

    # Old collection must still carry this workspace's points -- the failed
    # run must never have reached the old-collection delete.
    client = get_qdrant()
    old_points, _ = await client.scroll(COLLECTION, limit=10)
    assert any(p.payload["workspace_id"] == str(ws_id) for p in old_points)
