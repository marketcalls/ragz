import json

import pytest
from qdrant_client import models
from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from raghub.core.config import get_settings
from raghub.core.db import build_session_factory
from raghub.core.storage import build_storage
from raghub.modules.audit.models import AuditEvent
from raghub.modules.documents import ingest as ingest_module
from raghub.modules.documents.ingest import (
    mark_failed,
    run_chunk,
    run_delete,
    run_embed_upsert,
    run_parse,
)
from raghub.modules.documents.models import Document, IngestJob
from raghub.modules.documents.pipeline import IngestFailure
from raghub.modules.documents.service import create_from_upload
from raghub.modules.retrieval.client import COLLECTION, get_qdrant
from raghub.modules.retrieval.service import retrieve, update_document_current
from raghub.modules.tenancy.models import Group
from tests.modules.retrieval.test_retrieve import seed_workspace

TEXT = b"The flux capacitor requires 1.21 gigawatts.\n\nInvoice 0231 covers plutonium."

# Three ~1.7-2KB paragraphs: chunk_blocks (target_chars=2000) splits this into
# 3 chunks, so with _BATCH_SIZE monkeypatched to 1 the embed/upsert loop below
# runs 3 iterations — enough to land a mid-loop ACL race on the first batch
# while later batches are still pending.
_PARAGRAPH = "flux capacitor gigawatt invoice plutonium " * 40
BIG_TEXT = "\n\n".join(f"Section {i}. {_PARAGRAPH}" for i in range(3)).encode()


async def _upload(session: AsyncSession, name: str, data: bytes = TEXT) -> tuple:  # type: ignore[type-arg]
    ctx, ws = await seed_workspace(session, name)
    doc = await create_from_upload(session, ctx, ws.id, filename="n.txt",
                                   mime="text/plain", data=data)
    return ctx, ws, doc


async def test_full_runner_sequence_indexes_document(
    session: AsyncSession, qdrant_collection: None
) -> None:
    ctx, ws, doc = await _upload(session, "ing1")
    await run_parse(doc.id)
    await run_chunk(doc.id)
    await run_embed_upsert(doc.id)

    await session.refresh(doc)
    assert doc.status == "indexed" and doc.page_count == 1
    jobs = {j.stage: j for j in (await session.execute(
        select(IngestJob).where(IngestJob.document_id == doc.id))).scalars()}
    assert set(jobs) == {"parse", "chunk", "embed", "upsert"}
    assert all(j.finished_at is not None and j.progress == 1.0 for j in jobs.values())

    # Plan H: freshly-upserted points are is_current=False (invisible) until
    # promotion (Task 6). Real promotion doesn't exist yet, so this test
    # stands in for it via the sanctioned update_document_current path.
    await update_document_current(ctx.org_id, doc.id, is_current=True)
    result = await retrieve(session, ctx, ws.id, "invoice 0231")
    assert result.chunks and result.chunks[0].document_id == doc.id

    raw = await build_storage(get_settings()).get(doc.storage_key + ".chunks.json")
    assert json.loads(raw)  # chunk artifact persisted between stages


async def test_parse_failure_marks_document_failed(
    session: AsyncSession, stack_env: None
) -> None:
    ctx, ws = await seed_workspace(session, "ing2")
    doc = await create_from_upload(session, ctx, ws.id, filename="bad.xyz",
                                   mime="application/octet-stream", data=b"\x00junk")
    with pytest.raises(IngestFailure):
        await run_parse(doc.id)
    await session.refresh(doc)
    assert doc.status == "failed" and doc.error


async def test_mark_failed_records_reason(session: AsyncSession, stack_env: None) -> None:
    ctx, ws, doc = await _upload(session, "ing3")
    await mark_failed(doc.id, "boom after retries")
    await session.refresh(doc)
    assert doc.status == "failed" and doc.error == "boom after retries"


async def test_delete_propagates_everywhere(
    session: AsyncSession, qdrant_collection: None
) -> None:
    ctx, ws, doc = await _upload(session, "ing4")
    await run_parse(doc.id)
    await run_chunk(doc.id)
    await run_embed_upsert(doc.id)
    # Plan H: promote first so the delete-propagation check below is non-vacuous.
    await update_document_current(ctx.org_id, doc.id, is_current=True)

    await run_delete(doc.id, ctx.user_id)
    assert (await session.execute(
        select(Document).where(Document.id == doc.id))).scalar_one_or_none() is None
    result = await retrieve(session, ctx, ws.id, "invoice 0231")
    assert result.chunks == []
    actions = [e.action for e in (await session.execute(select(AuditEvent))).scalars()]
    assert "document.deleted" in actions
    await run_delete(doc.id, ctx.user_id)  # idempotent


async def test_embed_upsert_after_delete_leaves_no_orphaned_points(
    session: AsyncSession, qdrant_collection: None
) -> None:
    """Regression for the delete/ingest race: if run_delete wins and removes
    the document row before (or during) run_embed_upsert, the runner must not
    raise, must not mark the document indexed, and must not leave retrievable
    points behind in Qdrant."""
    ctx, ws, doc = await _upload(session, "ing5")
    await run_parse(doc.id)
    await run_chunk(doc.id)

    # Simulate run_delete having already completed the DB-row removal while
    # this ingest was in flight (delete is idempotent and races independently).
    await session.execute(sa_delete(Document).where(Document.id == doc.id))
    await session.commit()

    await run_embed_upsert(doc.id)  # must not raise

    count = await get_qdrant().count(
        COLLECTION,
        count_filter=models.Filter(
            must=[
                models.FieldCondition(
                    key="document_id", match=models.MatchValue(value=str(doc.id))
                )
            ]
        ),
        exact=True,
    )
    assert count.count == 0


async def test_embed_upsert_stamps_final_acl_after_race(
    session: AsyncSession, engine: AsyncEngine, qdrant_collection: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression for the ACL/ingest race: an ACL set on the PG row while a
    document is mid-ingest must not be lost. run_embed_upsert loads the doc
    row once before its batch loop; if an admin's ACL PUT lands in that
    window, update_document_acl (called by the PUT) only re-stamps points
    that already exist in Qdrant — any batch upserted afterward would carry
    the stale ACL captured at loop start unless the runner re-stamps against
    the row's CURRENT acl_group_ids right before marking the document
    indexed.

    To make the final re-stamp load-bearing (rather than a no-op because the
    ACL was already committed before the run started), the race is landed
    MID-LOOP: embed_batch is wrapped so that on its first invocation only, a
    second session commits the new ACL directly to the document row, then
    delegates to the real embed_batch. BIG_TEXT + _BATCH_SIZE=1 guarantees
    at least one more batch is upserted (with the stale, pre-race ACL) after
    that commit lands, so only the final re-stamp can make every point
    consistent. This asserts the invariant end-to-end without a scroll going
    through the (Task-4, not-yet-built) ACL-aware retrieval filter: every
    point for the document must carry the new, non-empty ACL."""
    ctx, ws, doc = await _upload(session, "ing6", data=BIG_TEXT)
    await run_parse(doc.id)
    await run_chunk(doc.id)

    group = Group(org_id=ctx.org_id, name="finance")
    session.add(group)
    await session.commit()
    new_acl = [group.id]

    monkeypatch.setattr(ingest_module, "_BATCH_SIZE", 1)

    real_embed_batch = ingest_module.embed_batch
    calls = 0

    async def racing_embed_batch(texts, dense_embedder):  # type: ignore[no-untyped-def]
        nonlocal calls
        calls += 1
        if calls == 1:
            factory = build_session_factory(engine)
            async with factory() as race_session:
                race_doc = (
                    await race_session.execute(
                        select(Document).where(Document.id == doc.id)
                    )
                ).scalar_one()
                race_doc.acl_group_ids = new_acl
                await race_session.commit()
        return await real_embed_batch(texts, dense_embedder)

    monkeypatch.setattr(ingest_module, "embed_batch", racing_embed_batch)

    await run_embed_upsert(doc.id)

    assert calls >= 2  # the race landed mid-loop, with more batches still to run

    await session.refresh(doc)
    assert doc.status == "indexed"

    points, _ = await get_qdrant().scroll(
        COLLECTION,
        scroll_filter=models.Filter(
            must=[
                models.FieldCondition(
                    key="document_id", match=models.MatchValue(value=str(doc.id))
                )
            ]
        ),
        limit=100,
        with_payload=True,
    )
    assert points  # the document actually indexed something
    for point in points:
        payload = point.payload or {}
        assert payload.get("acl_groups") == [str(group.id)]


async def test_embed_upsert_stamps_final_metadata_after_race(
    session: AsyncSession, engine: AsyncEngine, qdrant_collection: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same race as the ACL test above, for document metadata (final review):
    a Tags PUT landing mid-ingest restamps only already-upserted points; later
    batches carry the stale meta loaded at loop start unless the runner
    re-stamps from the fresh row before marking indexed."""
    ctx, ws, doc = await _upload(session, "ing7", data=BIG_TEXT)
    await run_parse(doc.id)
    await run_chunk(doc.id)

    monkeypatch.setattr(ingest_module, "_BATCH_SIZE", 1)
    real_embed_batch = ingest_module.embed_batch
    calls = 0

    async def racing_embed_batch(texts, dense_embedder):  # type: ignore[no-untyped-def]
        nonlocal calls
        calls += 1
        if calls == 1:
            factory = build_session_factory(engine)
            async with factory() as race_session:
                race_doc = (
                    await race_session.execute(
                        select(Document).where(Document.id == doc.id)
                    )
                ).scalar_one()
                race_doc.meta = {"doc_type": "policy"}
                await race_session.commit()
        return await real_embed_batch(texts, dense_embedder)

    monkeypatch.setattr(ingest_module, "embed_batch", racing_embed_batch)
    await run_embed_upsert(doc.id)
    assert calls >= 2  # race landed mid-loop with batches still to run

    points, _ = await get_qdrant().scroll(
        COLLECTION,
        scroll_filter=models.Filter(
            must=[
                models.FieldCondition(
                    key="document_id", match=models.MatchValue(value=str(doc.id))
                )
            ]
        ),
        limit=100,
        with_payload=True,
    )
    assert points
    assert all(p.payload["meta"] == {"doc_type": "policy"} for p in points)
