"""Async ingestion runners: orchestration + job status around the pure pipeline
stages. Called from Celery via asyncio.run (ADR-0001), so each runner owns its
engine lifecycle instead of sharing a loop-bound pool."""

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict
from uuid import UUID

from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from raghub.core.config import get_settings
from raghub.core.db import build_engine, build_session_factory, naive_utc
from raghub.core.storage import ObjectStorage, build_storage
from raghub.modules.audit.service import record_audit
from raghub.modules.documents.models import Document, IngestJob
from raghub.modules.documents.pipeline import (
    Chunk,
    IngestFailure,
    PageBlock,
    chunk_blocks,
    embed_batch,
    parse_bytes,
    upsert_points,
)
from raghub.modules.retrieval.embeddings import get_dense_embedder
from raghub.modules.retrieval.service import (
    delete_document_points,
    ensure_collection,
    update_document_acl,
)

_BATCH_SIZE = 32


@asynccontextmanager
async def _session() -> AsyncIterator[AsyncSession]:
    engine = build_engine(get_settings().database_url)
    try:
        async with build_session_factory(engine)() as session:
            yield session
    finally:
        await engine.dispose()


async def _get_document(session: AsyncSession, document_id: UUID) -> Document:
    doc = (
        await session.execute(select(Document).where(Document.id == document_id))
    ).scalar_one_or_none()
    if doc is None:
        raise IngestFailure(f"document {document_id} no longer exists")
    return doc


async def _start_stage(session: AsyncSession, document_id: UUID, stage: str) -> IngestJob:
    job = IngestJob(document_id=document_id, stage=stage, started_at=naive_utc())
    session.add(job)
    await session.commit()  # visible immediately for live UI progress
    return job


async def _finish_stage(session: AsyncSession, job: IngestJob, error: str | None = None) -> None:
    job.finished_at = naive_utc()
    job.error = error
    if error is None:
        job.progress = 1.0
    await session.commit()


async def _fail(session: AsyncSession, doc: Document, job: IngestJob, reason: str) -> None:
    doc.status = "failed"
    doc.error = reason[:1000]
    await _finish_stage(session, job, error=reason[:1000])


def _storage() -> ObjectStorage:
    return build_storage(get_settings())


async def run_parse(document_id: UUID) -> None:
    async with _session() as session:
        doc = await _get_document(session, document_id)
        job = await _start_stage(session, document_id, "parse")
        storage = _storage()
        try:
            data = await storage.get(doc.storage_key)
            blocks = await asyncio.to_thread(parse_bytes, data, doc.filename)
        except IngestFailure as exc:
            await _fail(session, doc, job, str(exc))
            raise
        await storage.put(
            doc.storage_key + ".blocks.json",
            json.dumps([asdict(b) for b in blocks]).encode(),
            content_type="application/json",
        )
        doc.status = "processing"
        doc.page_count = max(b.page for b in blocks)
        await _finish_stage(session, job)


async def run_chunk(document_id: UUID) -> None:
    async with _session() as session:
        doc = await _get_document(session, document_id)
        job = await _start_stage(session, document_id, "chunk")
        storage = _storage()
        raw = await storage.get(doc.storage_key + ".blocks.json")
        blocks = [PageBlock(**b) for b in json.loads(raw)]
        chunks = chunk_blocks(blocks)
        if not chunks:
            await _fail(session, doc, job, "chunking produced no chunks")
            raise IngestFailure("chunking produced no chunks")
        await storage.put(
            doc.storage_key + ".chunks.json",
            json.dumps([asdict(c) for c in chunks]).encode(),
            content_type="application/json",
        )
        await _finish_stage(session, job)


async def run_embed_upsert(document_id: UUID) -> None:
    """Stages 3+4 in one runner: embedding a batch and upserting it immediately
    avoids persisting vectors between tasks; ingest_jobs still shows both stages.

    run_delete is idempotent and doesn't coordinate with in-flight ingest, so it
    can win the race either before this runner starts or partway through it.
    Both must be handled without raising and without leaving retrievable points."""
    async with _session() as session:
        doc = (
            await session.execute(select(Document).where(Document.id == document_id))
        ).scalar_one_or_none()
        if doc is None:
            return  # deleted before we started: nothing was written, nothing to clean up
        org_id = doc.org_id  # captured now: doc may be gone by the time we re-check below
        embed_job = await _start_stage(session, document_id, "embed")
        upsert_job = await _start_stage(session, document_id, "upsert")
        raw = await _storage().get(doc.storage_key + ".chunks.json")
        chunks = [Chunk(**c) for c in json.loads(raw)]
        await ensure_collection()  # workspaces are bge-m3-locked in Phase 1
        dense_embedder = get_dense_embedder()
        done = 0
        for i in range(0, len(chunks), _BATCH_SIZE):
            batch = chunks[i : i + _BATCH_SIZE]
            dense, sparse = await embed_batch([c.text for c in batch], dense_embedder)
            await upsert_points(
                org_id=doc.org_id, workspace_id=doc.workspace_id, document_id=doc.id,
                mime=doc.mime, created_at=doc.created_at,
                acl_group_ids=[str(g) for g in (doc.acl_group_ids or [])],
                chunks=batch, dense=dense, sparse=sparse,
            )
            done += len(batch)
            embed_job.progress = upsert_job.progress = done / len(chunks)
            await session.commit()

        # A concurrent run_delete may have removed the row while we were
        # embedding/upserting (e.g. delete raced ahead and completed before
        # this task reached here). If so, the points we just wrote are
        # orphaned and retrievable despite the document no longer existing —
        # clean them up and skip marking indexed.
        # populate_existing: `doc` (and thus `still_exists`, same identity map
        # entry) was loaded once at the top of this function and this session
        # never expires on commit — without forcing a repopulate here, a
        # concurrent ACL PUT (different session) committed mid-loop would be
        # invisible to this SELECT even though its row is on disk.
        still_exists = (
            await session.execute(
                select(Document)
                .where(Document.id == document_id)
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if still_exists is None:
            await delete_document_points(org_id, document_id)
            return

        # An ACL admin PUT racing mid-ingest may have stamped only the points
        # upserted so far (update_document_acl re-stamps existing points, not
        # ones written by later batches) — or landed between the last batch's
        # upsert and here. Re-stamp against the row's CURRENT acl_group_ids
        # (freshly repopulated above) so every point for this document
        # reflects the latest PG state before the document is marked indexed
        # and becomes retrievable.
        await update_document_acl(org_id, document_id, still_exists.acl_group_ids)

        doc.status = "indexed"
        await _finish_stage(session, embed_job)
        await _finish_stage(session, upsert_job)


async def run_delete(document_id: UUID, actor_id: UUID | None) -> None:
    """One task propagating deletion: Qdrant points → MinIO objects → Postgres
    rows, with an audit entry (spec §2.3, DOC-8). Idempotent."""
    async with _session() as session:
        doc = (
            await session.execute(select(Document).where(Document.id == document_id))
        ).scalar_one_or_none()
        if doc is None:
            return
        await delete_document_points(doc.org_id, document_id)
        storage = _storage()
        for key in (doc.storage_key, doc.storage_key + ".blocks.json",
                    doc.storage_key + ".chunks.json"):
            await storage.delete(key)
        await session.execute(sa_delete(IngestJob).where(IngestJob.document_id == document_id))
        await record_audit(session, org_id=doc.org_id, actor_id=actor_id,
                           action="document.deleted", target_type="document",
                           target_id=str(document_id))
        await session.delete(doc)
        await session.commit()


async def mark_failed(document_id: UUID, reason: str) -> None:
    """Terminal-failure hook for the Celery on_failure callback."""
    async with _session() as session:
        doc = (
            await session.execute(select(Document).where(Document.id == document_id))
        ).scalar_one_or_none()
        if doc is not None:
            doc.status = "failed"
            doc.error = reason[:1000]
            await session.commit()
