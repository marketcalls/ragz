from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from ragz.core.errors import WorkspaceAccessDenied
from ragz.modules.retrieval.client import COLLECTION, get_qdrant
from ragz.modules.retrieval.service import get_chunks_by_refs, list_document_chunks
from tests.modules.retrieval.test_retrieve import seed_workspace, upsert_texts


async def test_list_document_chunks_ordered_and_scored_as_pinned(
    session: AsyncSession, qdrant_collection: None
) -> None:
    ctx, ws = await seed_workspace(session, "chunksOrg")
    doc_id = UUID(await upsert_texts(ctx, ws, ["page one text", "page two text"]))
    chunks = await list_document_chunks(ctx, ws.id, doc_id, collection_name=COLLECTION)
    assert [(c.page, c.chunk_index) for c in chunks] == [(1, 0), (2, 1)]
    assert all(c.score == 1.0 for c in chunks)
    assert await list_document_chunks(ctx, ws.id, uuid4(), collection_name=COLLECTION) == []


async def test_get_chunks_by_refs_resolves_in_ref_order(
    session: AsyncSession, qdrant_collection: None
) -> None:
    ctx, ws = await seed_workspace(session, "refsOrg")
    doc_id = await upsert_texts(ctx, ws, ["first chunk", "second chunk"])
    refs = [f"{doc_id}:2:1", f"{doc_id}:1:0"]
    chunks = await get_chunks_by_refs(ctx, ws.id, refs, collection_name=COLLECTION)
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
    chunks = await get_chunks_by_refs(ctx, ws.id, refs, collection_name=COLLECTION)
    assert [c.text for c in chunks] == ["only chunk"]


async def test_list_document_chunks_denies_non_member_user(
    session: AsyncSession, qdrant_collection: None
) -> None:
    """Review round 1: the in-reader membership guard. A "user"-role ctx not a
    member of workspace_id must be rejected before the filter even runs, same
    as retrieve()'s gate."""
    ctx, ws = await seed_workspace(session, "chunksNonMemberOrg", member=False)
    with pytest.raises(WorkspaceAccessDenied):
        await list_document_chunks(ctx, ws.id, uuid4(), collection_name=COLLECTION)


async def test_list_document_chunks_allows_admin_without_membership(
    session: AsyncSession, qdrant_collection: None
) -> None:
    """Admin/superadmin pass the in-reader guard; org fencing still applies
    via the tenant filter itself."""
    ctx, ws = await seed_workspace(session, "chunksAdminOrg", role="admin", member=False)
    assert await list_document_chunks(ctx, ws.id, uuid4(), collection_name=COLLECTION) == []


async def test_get_chunks_by_refs_denies_non_member_user(
    session: AsyncSession, qdrant_collection: None
) -> None:
    """Review round 1: same in-reader membership guard on the refs reader."""
    ctx, ws = await seed_workspace(session, "refsNonMemberOrg", member=False)
    with pytest.raises(WorkspaceAccessDenied):
        await get_chunks_by_refs(ctx, ws.id, [f"{uuid4()}:1:0"], collection_name=COLLECTION)


async def test_get_chunks_by_refs_allows_admin_without_membership(
    session: AsyncSession, qdrant_collection: None
) -> None:
    ctx, ws = await seed_workspace(session, "refsAdminOrg", role="admin", member=False)
    assert (
        await get_chunks_by_refs(ctx, ws.id, [f"{uuid4()}:1:0"], collection_name=COLLECTION) == []
    )


async def test_get_chunks_by_refs_paginates_across_scroll_pages(
    session: AsyncSession, qdrant_collection: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review round 1: before the fix, get_chunks_by_refs read exactly ONE
    _SCROLL_PAGE and dropped the returned offset, so a ref living past the
    first page silently vanished. Force tiny pages so a 5-chunk document
    needs 3 scroll round-trips, and request the very LAST chunk -- it must
    still resolve, proving the offset loop (like list_document_chunks's) is
    now in place."""
    monkeypatch.setattr("ragz.modules.retrieval.service._SCROLL_PAGE", 2)
    ctx, ws = await seed_workspace(session, "refsPageOrg")
    texts = [f"chunk number {i}" for i in range(5)]
    doc_id = await upsert_texts(ctx, ws, texts)
    refs = [f"{doc_id}:5:4"]  # page=i+1, chunk_index=i -> last text, last page
    chunks = await get_chunks_by_refs(ctx, ws.id, refs, collection_name=COLLECTION)
    assert [c.text for c in chunks] == ["chunk number 4"]


async def test_get_chunks_by_refs_stops_scrolling_once_all_refs_found(
    session: AsyncSession, qdrant_collection: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The pagination loop must stop as soon as every requested ref for a
    document has been seen, not scroll every remaining page unconditionally
    -- verified by counting the underlying scroll() calls."""
    monkeypatch.setattr("ragz.modules.retrieval.service._SCROLL_PAGE", 2)
    ctx, ws = await seed_workspace(session, "refsEarlyStopOrg")
    texts = [f"chunk number {i}" for i in range(5)]
    # Sequential point ids: Qdrant scroll order is point-id order, so chunk 0 is
    # deterministically on scroll page 1 (page size 2). Random ids made this flaky.
    ids = [f"00000000-0000-0000-0000-00000000000{i}" for i in range(5)]
    doc_id = await upsert_texts(ctx, ws, texts, point_ids=ids)
    refs = [f"{doc_id}:1:0"]  # first chunk only, satisfied by the first page
    client = get_qdrant()
    orig_scroll = client.scroll
    calls: list[int] = []

    async def counting_scroll(*args: object, **kwargs: object):  # type: ignore[no-untyped-def]
        calls.append(1)
        return await orig_scroll(*args, **kwargs)

    monkeypatch.setattr(client, "scroll", counting_scroll)
    chunks = await get_chunks_by_refs(ctx, ws.id, refs, collection_name=COLLECTION)
    assert [c.text for c in chunks] == ["chunk number 0"]
    assert len(calls) == 1  # stopped after page 1; did not scroll pages 2-3
