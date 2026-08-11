"""Adversarial leak tests for version-swap visibility (DOC-5, iron rule 2).

Extends tests/isolation/test_tenant_isolation.py: a version being superseded,
demoted, or merely in-flight must never leak through retrieve().
"""

from sqlalchemy.ext.asyncio import AsyncSession

from ragz.core.app_settings import set_app_setting
from ragz.core.config import get_settings
from ragz.modules.documents.ingest import run_chunk, run_embed_upsert, run_parse
from ragz.modules.documents.pipeline import PageBlock, chunk_blocks, embed_batch, upsert_points
from ragz.modules.documents.service import create_from_upload, set_approved
from ragz.modules.models.models import LOCAL_EMBEDDING_MODEL_ID
from ragz.modules.retrieval.client import COLLECTION
from ragz.modules.retrieval.embeddings import get_dense_embedder
from ragz.modules.retrieval.service import ensure_collection, retrieve
from tests.modules.retrieval.test_retrieve import seed_workspace


async def _index(session, ctx, ws, filename, text):  # type: ignore[no-untyped-def]
    # Plain .txt fixture content -- anydoc (the install-wide default) does not
    # parse .txt at all; explicit docling keeps this version-isolation suite
    # exercising the real pipeline instead of failing on an unrelated gap.
    await set_app_setting(session, "document_parser", "docling")
    doc = await create_from_upload(
        session, ctx, ws.id, filename=filename, mime="text/plain", data=text.encode()
    )
    await run_parse(doc.id)
    await run_chunk(doc.id)
    await run_embed_upsert(doc.id)
    await session.refresh(doc)
    return doc


async def test_superseded_version_unretrievable(
    session: AsyncSession, qdrant_collection: None
) -> None:
    """v1 ingested, then re-uploaded (same filename) as v2 with different
    content. After v2 indexes (and is promoted), querying with v1's exact
    text as the lure must never surface v1: v1's id must not appear, v1's
    secret must not appear in any chunk text, and v2's chunk MUST appear
    (non-vacuous), carrying version==2."""
    ctx, ws = await seed_workspace(session, "verIso1")
    v1 = await _index(session, ctx, ws, "report.txt", "the muster point is DOCK 4")
    v2 = await _index(session, ctx, ws, "report.txt", "the muster point is GATE 9")

    result = await retrieve(session, ctx, ws.id, "the muster point is DOCK 4", top_k=10)
    returned_docs = {c.document_id for c in result.chunks}
    assert v1.id not in returned_docs
    assert all("DOCK 4" not in c.text for c in result.chunks)
    assert v2.id in returned_docs  # non-vacuous: the lineage IS still retrievable
    assert all(c.version == 2 for c in result.chunks if c.document_id == v2.id)


async def test_approved_old_version_served_not_newer_draft(
    session: AsyncSession, qdrant_collection: None
) -> None:
    """v1 approved, v2 indexed but unapproved -> retrieve() returns v1
    content only; v2's draft content never surfaces, even transiently."""
    ctx, ws = await seed_workspace(session, "verIso2")
    v1 = await _index(session, ctx, ws, "report.txt", "the muster point is DOCK 4")
    await set_approved(session, ctx, v1.id, True)
    v2 = await _index(session, ctx, ws, "report.txt", "the muster point is GATE 9")

    result = await retrieve(session, ctx, ws.id, "muster point", top_k=10)
    returned_docs = {c.document_id for c in result.chunks}
    assert v1.id in returned_docs
    assert v2.id not in returned_docs
    assert all("GATE 9" not in c.text for c in result.chunks)


async def test_in_flight_points_invisible(
    session: AsyncSession, qdrant_collection: None
) -> None:
    """Upsert v2's points directly (bypassing run_embed_upsert's promote_lineage
    tail) without promoting: retrieve() must still return only v1. Kills any
    mutant that upserts new versions visible-by-default (upsert_points'
    is_current default flipped, or the current_only filter dropped)."""
    ctx, ws = await seed_workspace(session, "verIso3")
    v1 = await _index(session, ctx, ws, "report.txt", "the muster point is DOCK 4")

    v2 = await create_from_upload(
        session, ctx, ws.id, filename="report.txt",
        mime="text/plain", data=b"the muster point is GATE 9",
    )
    blocks = [PageBlock(page=1, text="the muster point is GATE 9", kind="text")]
    chunks = chunk_blocks(blocks)
    # DOC-10: inline v2-upsert bypass, kept in step with production's
    # explicit collection_name/model-parameterized get_dense_embedder --
    # ws here carries the seeded default (LOCAL_EMBEDDING_MODEL_ID/COLLECTION).
    await ensure_collection(COLLECTION, get_settings().embedding_dim)
    dense_embedder = get_dense_embedder(
        LOCAL_EMBEDDING_MODEL_ID, provider_kind="tei", litellm_model_name="local-embeddings"
    )
    dense, sparse = await embed_batch([c.text for c in chunks], dense_embedder)
    await upsert_points(
        org_id=ctx.org_id, workspace_id=ws.id, document_id=v2.id, mime=v2.mime,
        created_at=v2.created_at, acl_group_ids=[], chunks=chunks,
        dense=dense, sparse=sparse, version=v2.version, meta=None,
        collection_name=COLLECTION,
    )  # is_current defaults False -- points exist but must stay invisible

    result = await retrieve(session, ctx, ws.id, "the muster point is GATE 9", top_k=10)
    returned_docs = {c.document_id for c in result.chunks}
    assert v2.id not in returned_docs
    assert all("GATE 9" not in c.text for c in result.chunks)
    assert v1.id in returned_docs  # non-vacuous: the lineage still resolves to v1
