import asyncio
from uuid import uuid4

import pytest
from qdrant_client import models
from sqlalchemy.ext.asyncio import AsyncSession

from raghub.core.config import get_settings
from raghub.core.errors import WorkspaceAccessDenied
from raghub.modules.auth.models import User
from raghub.modules.models.models import LOCAL_EMBEDDING_MODEL_ID
from raghub.modules.retrieval import service as retrieval_service
from raghub.modules.retrieval.client import COLLECTION, get_qdrant
from raghub.modules.retrieval.embeddings import embed_sparse, get_dense_embedder
from raghub.modules.retrieval.service import (
    RetrievedChunk,
    _dedupe_hq,
    delete_document_points,
    ensure_collection,
    retrieve,
)
from raghub.modules.tenancy.context import TenantContext
from raghub.modules.tenancy.models import Organization, Workspace, WorkspaceMember

# The dense embedder used by test-seeded points (upsert_texts and friends):
# always resolves to HashDenseEmbedder under RAGHUB_EMBEDDING_BACKEND=hash
# (stack_env), but get_dense_embedder's signature (DOC-10, Task 2) now
# requires a model identity regardless of backend -- these args mirror the
# LOCAL_EMBEDDING_MODEL_ID row the shared `engine` fixture seeds.
_LOCAL_MODEL_KW = {
    "model_id": LOCAL_EMBEDDING_MODEL_ID,
    "provider_kind": "tei",
    "litellm_model_name": "local-embeddings",
}


async def seed_workspace(
    session: AsyncSession, org_name: str, *, role: str = "user", member: bool = True,
    min_score: float = 0.0, top_k: int = 8, rerank_enabled: bool = False,
) -> tuple[TenantContext, Workspace]:
    org = Organization(name=org_name)
    session.add(org)
    await session.flush()
    ws = Workspace(org_id=org.id, name="ws", min_score=min_score,
                   top_k=top_k, rerank_enabled=rerank_enabled)
    user = User(org_id=org.id, email=f"u@{org_name}.com", password_hash="x", role=role)  # noqa: S106
    session.add_all([ws, user])
    await session.flush()
    if member:
        session.add(WorkspaceMember(workspace_id=ws.id, user_id=user.id))
    await session.commit()
    ctx = TenantContext(
        user_id=user.id, org_id=org.id, role=role,
        workspace_ids=frozenset({ws.id}) if member else frozenset(),
    )
    return ctx, ws


async def upsert_texts(
    ctx: TenantContext, ws: Workspace, texts: list[str],
    point_ids: list[str] | None = None,
) -> str:
    """Test seeding via raw points; production code goes through the pipeline.

    point_ids: optional deterministic ids — Qdrant scroll order is point-id order,
    so tests asserting page positions must pass sequential ids (see early-stop test).
    """
    document_id = str(uuid4())
    dense = await get_dense_embedder(**_LOCAL_MODEL_KW).embed(texts)
    sparse = await asyncio.to_thread(embed_sparse, texts)
    points = [
        models.PointStruct(
            id=point_ids[i] if point_ids else str(uuid4()),
            vector={"dense": d, "sparse": s},
            payload={"tenant_id": str(ctx.org_id), "workspace_id": str(ws.id),
                     "document_id": document_id, "page": i + 1, "chunk_index": i,
                     "text": t, "doc_type": "text/plain", "date": "2026-07-18",
                     "acl_groups": []},
        )
        for i, (t, d, s) in enumerate(zip(texts, dense, sparse, strict=True))
    ]
    await get_qdrant().upsert(COLLECTION, points=points, wait=True)
    return document_id


async def test_retrieve_returns_matching_chunk(
    session: AsyncSession, qdrant_collection: None
) -> None:
    ctx, ws = await seed_workspace(session, "orga")
    await upsert_texts(ctx, ws, ["the flux capacitor requires 1.21 gigawatts",
                                 "unrelated kumquat farming notes"])
    result = await retrieve(session, ctx, ws.id, "flux capacitor gigawatts", top_k=2)
    assert not result.no_answer
    assert result.chunks[0].text.startswith("the flux capacitor")
    assert result.chunks[0].page == 1 and result.chunks[0].chunk_index == 0


async def test_min_score_triggers_no_answer_with_nearest(
    session: AsyncSession, qdrant_collection: None
) -> None:
    ctx, ws = await seed_workspace(session, "orgb", min_score=0.99)
    await upsert_texts(ctx, ws, ["some vaguely related text about invoices"])
    result = await retrieve(session, ctx, ws.id, "completely different query terms")
    assert result.no_answer
    assert result.chunks  # nearest sources still surfaced (CHAT-9)


async def test_empty_workspace_is_no_answer(
    session: AsyncSession, qdrant_collection: None
) -> None:
    ctx, ws = await seed_workspace(session, "orgc")
    result = await retrieve(session, ctx, ws.id, "anything")
    assert result.no_answer and result.chunks == []


async def test_retrieve_uses_workspace_specific_collection(
    session: AsyncSession, qdrant_collection: None
) -> None:
    """The whole point of DOC-10: a workspace on a non-seeded embedding model
    must be queried against ITS OWN collection, not the module's COLLECTION
    constant -- this is the exact bug the research for this plan found
    (retrieve() called ensure_collection(ws.embedding_model) but then
    hardcoded COLLECTION in both client.query_points calls). Pre-fix, this
    test fails outright: Workspace no longer even has an `embedding_model`
    attribute (Task 5 replaced it with `embedding_model_id`), so the old
    `await ensure_collection(ws.embedding_model)` line raises AttributeError
    before either query_points call is reached."""
    from raghub.modules.models import service as models_service

    ctx, ws = await seed_workspace(session, "orgh")
    # Seed the workspace's DEFAULT (bge-m3) collection with a matching chunk --
    # if retrieve() ever fell back to querying COLLECTION regardless of the
    # workspace's actual model, this text would be findable and the test below
    # would wrongly pass.
    await upsert_texts(ctx, ws, ["the flux capacitor requires 1.21 gigawatts"])

    other_model = await models_service.create_model(
        session, ctx,
        litellm_model_name="other-embed", display_name="Other",
        provider_kind="tei", base_url=None, api_key=None,
        settings=get_settings(), modality="embedding", dimension=1024,
    )
    ws.embedding_model_id = other_model.id
    await session.commit()

    # other_model's collection is brand new and empty -- a workspace pointed
    # at it must find NOTHING, even though a matching chunk exists in the
    # seeded default collection.
    result = await retrieve(session, ctx, ws.id, "flux capacitor gigawatts")
    assert result.chunks == []
    assert result.no_answer
    assert await get_qdrant().collection_exists(other_model.collection_name)
    assert other_model.collection_name != COLLECTION


async def test_non_member_denied(session: AsyncSession, qdrant_collection: None) -> None:
    ctx, ws = await seed_workspace(session, "orgd", member=False)
    with pytest.raises(WorkspaceAccessDenied):
        await retrieve(session, ctx, ws.id, "anything")


async def test_admin_without_membership_allowed(
    session: AsyncSession, qdrant_collection: None
) -> None:
    ctx, ws = await seed_workspace(session, "orge", role="admin", member=False)
    result = await retrieve(session, ctx, ws.id, "anything")
    assert result.chunks == []


async def test_delete_document_points(session: AsyncSession, qdrant_collection: None) -> None:
    ctx, ws = await seed_workspace(session, "orgf")
    doc_id = await upsert_texts(ctx, ws, ["target text to delete"])
    from uuid import UUID
    await delete_document_points(ctx.org_id, UUID(doc_id), collection_name=COLLECTION)
    result = await retrieve(session, ctx, ws.id, "target text to delete")
    assert result.chunks == []


async def test_delete_restricted_document_points(
    session: AsyncSession, qdrant_collection: None
) -> None:
    """Verify deletion of a RESTRICTED document via delete_document_points:
    an unfiltered scroll must show zero points for that doc (maintenance posture
    ignores ACL). Kills the mutant hard-coding a groupset in delete's filter."""
    from uuid import UUID

    from raghub.modules.retrieval.client import COLLECTION

    ctx, ws = await seed_workspace(session, "orgg")
    # Seed a restricted document with ACL payload (acl_groups=[finance_group_id])
    document_id = str(uuid4())
    finance_id = uuid4()
    dense = await get_dense_embedder(**_LOCAL_MODEL_KW).embed(["restricted finance data"])
    sparse = await asyncio.to_thread(embed_sparse, ["restricted finance data"])
    points = [
        models.PointStruct(
            id=str(uuid4()),
            vector={"dense": dense[0], "sparse": sparse[0]},
            payload={
                "tenant_id": str(ctx.org_id),
                "workspace_id": str(ws.id),
                "document_id": document_id,
                "page": 1,
                "chunk_index": 0,
                "text": "restricted finance data",
                "doc_type": "text/plain",
                "date": "2026-07-18",
                "acl_groups": [str(finance_id)],  # non-empty ACL payload
            },
        )
    ]
    await get_qdrant().upsert(COLLECTION, points=points, wait=True)

    # Delete the document via delete_document_points (maintenance path)
    await delete_document_points(ctx.org_id, UUID(document_id), collection_name=COLLECTION)

    # Unfiltered scroll must show zero points for that document
    all_points, _ = await get_qdrant().scroll(COLLECTION, limit=100)
    doc_points = [p for p in all_points if (p.payload or {}).get("document_id") == document_id]
    assert doc_points == []


async def test_delete_points_tolerates_missing_collection(stack_env: None) -> None:
    """Deleting a never-indexed document must be a no-op, not a Qdrant 404 (smoke regression).

    Uses stack_env (NOT the bare qdrant_url fixture) so get_qdrant() is redirected to
    the test container — otherwise this test's delete_collection would hit whatever
    Qdrant ambient settings point at (it once wiped the dev compose collection).
    """
    from uuid import uuid4

    from raghub.modules.retrieval.client import COLLECTION, get_qdrant
    from raghub.modules.retrieval.service import delete_document_points

    client = get_qdrant()
    if await client.collection_exists(COLLECTION):
        await client.delete_collection(COLLECTION)
    await delete_document_points(uuid4(), uuid4(), collection_name=COLLECTION)  # must not raise


def test_dedupe_hq_keeps_max_score_per_chunk_ref() -> None:
    doc_id = uuid4()
    parent = RetrievedChunk(document_id=doc_id, page=1, chunk_index=0, text="t", score=0.4)
    hq_hit = RetrievedChunk(document_id=doc_id, page=1, chunk_index=0, text="t", score=0.9)
    other = RetrievedChunk(document_id=doc_id, page=1, chunk_index=1, text="u", score=0.5)
    result = _dedupe_hq([parent, hq_hit, other])
    assert len(result) == 2
    kept = next(r for r in result if r.chunk_index == 0)
    assert kept.score == 0.9


async def test_ensure_collection_heal_branch_runs_once_per_process(
    qdrant_collection: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """qdrant_collection already ensures the collection exists (via a prior
    ensure_collection() call in the fixture), so the calls made here run the
    heal (else) branch. _HEALED is module-level and persists across the test
    process, so it's reset here for a deterministic first-touch state."""
    monkeypatch.setattr(retrieval_service, "_HEALED", set())
    client = get_qdrant()
    calls: list[int] = []
    original = client.create_payload_index

    async def counting(*a, **kw):  # type: ignore[no-untyped-def]
        calls.append(1)
        return await original(*a, **kw)

    monkeypatch.setattr(client, "create_payload_index", counting)
    await ensure_collection(COLLECTION, get_settings().embedding_dim)
    await ensure_collection(COLLECTION, get_settings().embedding_dim)
    await ensure_collection(COLLECTION, get_settings().embedding_dim)
    assert len(calls) == 2  # exactly the first call's 2 heal indexes, never repeated


def test_dedupe_hq_noop_when_no_duplicates() -> None:
    doc_id = uuid4()
    chunks = [
        RetrievedChunk(document_id=doc_id, page=1, chunk_index=0, text="a", score=0.5),
        RetrievedChunk(document_id=doc_id, page=1, chunk_index=1, text="b", score=0.3),
    ]
    assert _dedupe_hq(chunks) == chunks
