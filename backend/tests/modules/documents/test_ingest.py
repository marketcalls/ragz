import json

import pytest
from qdrant_client import models
from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from raghub.core.config import get_settings
from raghub.core.storage import build_storage
from raghub.modules.audit.models import AuditEvent
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
from raghub.modules.retrieval.service import retrieve
from raghub.modules.tenancy.models import Group
from tests.modules.retrieval.test_retrieve import seed_workspace

TEXT = b"The flux capacitor requires 1.21 gigawatts.\n\nInvoice 0231 covers plutonium."


async def _upload(session: AsyncSession, name: str) -> tuple:  # type: ignore[type-arg]
    ctx, ws = await seed_workspace(session, name)
    doc = await create_from_upload(session, ctx, ws.id, filename="n.txt",
                                   mime="text/plain", data=TEXT)
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
    session: AsyncSession, qdrant_collection: None
) -> None:
    """Regression for the ACL/ingest race: an ACL set on the PG row while a
    document is mid-ingest must not be lost. run_embed_upsert loads the doc
    row once before its batch loop; if an admin's ACL PUT lands in that
    window, update_document_acl (called by the PUT) only re-stamps points
    that already exist in Qdrant — any batch upserted afterward would carry
    the stale ACL captured at loop start unless the runner re-stamps against
    the row's CURRENT acl_group_ids right before marking the document
    indexed. This asserts the invariant end-to-end without a scroll going
    through the (Task-4, not-yet-built) ACL-aware retrieval filter: every
    point for the document must carry the new, non-empty ACL."""
    ctx, ws, doc = await _upload(session, "ing6")
    await run_parse(doc.id)
    await run_chunk(doc.id)

    group = Group(org_id=ctx.org_id, name="finance")
    session.add(group)
    await session.flush()
    doc.acl_group_ids = [group.id]
    await session.commit()

    await run_embed_upsert(doc.id)

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
