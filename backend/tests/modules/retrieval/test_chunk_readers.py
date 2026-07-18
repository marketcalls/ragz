from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from raghub.modules.retrieval.service import get_chunks_by_refs, list_document_chunks
from tests.modules.retrieval.test_retrieve import seed_workspace, upsert_texts


async def test_list_document_chunks_ordered_and_scored_as_pinned(
    session: AsyncSession, qdrant_collection: None
) -> None:
    ctx, ws = await seed_workspace(session, "chunksOrg")
    doc_id = UUID(await upsert_texts(ctx, ws, ["page one text", "page two text"]))
    chunks = await list_document_chunks(ctx, ws.id, doc_id)
    assert [(c.page, c.chunk_index) for c in chunks] == [(1, 0), (2, 1)]
    assert all(c.score == 1.0 for c in chunks)
    assert await list_document_chunks(ctx, ws.id, uuid4()) == []


async def test_get_chunks_by_refs_resolves_in_ref_order(
    session: AsyncSession, qdrant_collection: None
) -> None:
    ctx, ws = await seed_workspace(session, "refsOrg")
    doc_id = await upsert_texts(ctx, ws, ["first chunk", "second chunk"])
    refs = [f"{doc_id}:2:1", f"{doc_id}:1:0"]
    chunks = await get_chunks_by_refs(ctx, ws.id, refs)
    assert [c.text for c in chunks] == ["second chunk", "first chunk"]
    assert all(c.score == 0.0 for c in chunks)  # backfilled, not a similarity hit


async def test_get_chunks_by_refs_drops_malformed_unknown_and_duplicates(
    session: AsyncSession, qdrant_collection: None
) -> None:
    ctx, ws = await seed_workspace(session, "refsJunkOrg")
    doc_id = await upsert_texts(ctx, ws, ["only chunk"])
    refs = [
        "not-a-ref", "a:b:c", f"{uuid4()}:1:0",            # malformed / unknown
        f"{doc_id}:1:0", f"{doc_id}:1:0", f"{doc_id}:9:9",  # dup + missing index
    ]
    chunks = await get_chunks_by_refs(ctx, ws.id, refs)
    assert [c.text for c in chunks] == ["only chunk"]
