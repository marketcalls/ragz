import json

import pytest
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
from raghub.modules.retrieval.service import retrieve
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
