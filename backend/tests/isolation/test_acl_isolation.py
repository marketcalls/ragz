"""Adversarial ACL leak tests (RBAC-5). Run on every PR.

Same contract as test_tenant_isolation.py: a failure here is a security
incident, not a flake. Every test seeds through the REAL upload+ingest
pipeline and queries with the restricted document's exact secret as the lure.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from raghub.modules.documents.service import set_document_acl
from raghub.modules.retrieval.service import retrieve
from tests.isolation.conftest import ingest_text, seed_acl_workspace

RESTRICTED = "finance secret: the acquisition price is 4400"
UNRESTRICTED = "cafeteria notice: the lunch menu changes on friday"


async def _seed(session: AsyncSession):  # type: ignore[no-untyped-def]
    ctx_in, ctx_out, ctx_admin, ws, finance = await seed_acl_workspace(session)
    restricted = await ingest_text(session, ctx_admin, ws, "restricted.txt", RESTRICTED)
    await set_document_acl(session, ctx_admin, restricted.id, [finance.id])
    # ACL set BEFORE embed in half the flows and AFTER in this one — set_payload
    # restamps the already-upserted points, so both orders must converge.
    open_doc = await ingest_text(session, ctx_admin, ws, "open.txt", UNRESTRICTED)
    return ctx_in, ctx_out, ctx_admin, ws, finance, restricted, open_doc


async def test_user_outside_group_never_retrieves_restricted_doc(
    session: AsyncSession, qdrant_collection: None
) -> None:
    ctx_in, ctx_out, _, ws, _, restricted, open_doc = await _seed(session)
    result = await retrieve(session, ctx_out, ws.id, RESTRICTED, top_k=10)
    returned = {c.document_id for c in result.chunks}
    assert restricted.id not in returned
    assert open_doc.id in returned  # not a vacuous empty-result pass
    assert all("4400" not in c.text for c in result.chunks)


async def test_group_member_retrieves_restricted_doc(
    session: AsyncSession, qdrant_collection: None
) -> None:
    ctx_in, _, _, ws, _, restricted, _ = await _seed(session)
    result = await retrieve(session, ctx_in, ws.id, RESTRICTED, top_k=10)
    assert restricted.id in {c.document_id for c in result.chunks}


async def test_admin_bypasses_acl(
    session: AsyncSession, qdrant_collection: None
) -> None:
    """Documented RBAC-5 posture: admins manage the library and see all of it.
    The admin ctx carries NO groups, so a pass here proves the bypass comes
    from the role check in retrieve(), not from accidental group membership."""
    _, _, ctx_admin, ws, _, restricted, _ = await _seed(session)
    result = await retrieve(session, ctx_admin, ws.id, RESTRICTED, top_k=10)
    assert restricted.id in {c.document_id for c in result.chunks}


async def test_acl_change_reindexes_live_points(
    session: AsyncSession, qdrant_collection: None
) -> None:
    """Restrict-after-index and clear-after-restrict both take effect without
    re-embedding — the set_payload re-index path end to end."""
    ctx_in, ctx_out, ctx_admin, ws, finance, _, open_doc = await _seed(session)
    before = await retrieve(session, ctx_out, ws.id, UNRESTRICTED, top_k=10)
    assert open_doc.id in {c.document_id for c in before.chunks}

    await set_document_acl(session, ctx_admin, open_doc.id, [finance.id])
    locked = await retrieve(session, ctx_out, ws.id, UNRESTRICTED, top_k=10)
    assert open_doc.id not in {c.document_id for c in locked.chunks}
    still = await retrieve(session, ctx_in, ws.id, UNRESTRICTED, top_k=10)
    assert open_doc.id in {c.document_id for c in still.chunks}

    await set_document_acl(session, ctx_admin, open_doc.id, None)
    reopened = await retrieve(session, ctx_out, ws.id, UNRESTRICTED, top_k=10)
    assert open_doc.id in {c.document_id for c in reopened.chunks}


async def test_chunk_readers_respect_acl(
    session: AsyncSession, qdrant_collection: None
) -> None:
    """Plan E's side doors honor RBAC-5 too: pinned-doc injection
    (list_document_chunks) and citation backfill (get_chunks_by_refs) must not
    hand a restricted document's chunks to a non-member — even via a chunk_ref
    the outsider legitimately holds from before the doc was restricted."""
    from raghub.modules.retrieval.service import get_chunks_by_refs, list_document_chunks

    ctx_in, ctx_out, _, ws, _, restricted, _ = await _seed(session)
    assert await list_document_chunks(ctx_out, ws.id, restricted.id) == []
    assert await get_chunks_by_refs(ctx_out, ws.id, [f"{restricted.id}:1:0"]) == []
    # Not vacuous: the group member resolves both paths.
    assert [c.document_id for c in await list_document_chunks(ctx_in, ws.id, restricted.id)] \
        == [restricted.id]
    assert [c.document_id for c in
            await get_chunks_by_refs(ctx_in, ws.id, [f"{restricted.id}:1:0"])] \
        == [restricted.id]
