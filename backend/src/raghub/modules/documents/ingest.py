"""Async ingestion runners: orchestration + job status around the pure pipeline
stages. Called from Celery via asyncio.run (ADR-0001), so each runner owns its
engine lifecycle instead of sharing a loop-bound pool."""

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict
from uuid import UUID

import structlog
from qdrant_client import models as qdrant_models
from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from raghub.core.config import get_settings
from raghub.core.db import build_engine, build_session_factory, naive_utc
from raghub.core.storage import ObjectStorage, build_storage
from raghub.modules.audit.service import record_audit
from raghub.modules.chat.llm import LiteLLMStreamer, LLMCompleter
from raghub.modules.documents import service as documents_service
from raghub.modules.documents.enrichment import enrich_chunk
from raghub.modules.documents.models import Document, IngestJob
from raghub.modules.documents.pipeline import (
    Chunk,
    IngestFailure,
    PageBlock,
    chunk_blocks,
    embed_batch,
    parse_bytes,
    upsert_hq_points,
    upsert_points,
)
from raghub.modules.models import service as models_service
from raghub.modules.quotas import service as quota_service
from raghub.modules.retrieval.embeddings import get_dense_embedder
from raghub.modules.retrieval.service import (
    delete_document_points,
    ensure_collection,
    update_document_acl,
    update_document_metadata,
)
from raghub.modules.tenancy.models import Workspace

log = structlog.get_logger()

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
            settings = get_settings()
            blocks = await asyncio.to_thread(
                parse_bytes, data, doc.filename,
                ocr_enabled=settings.ocr_enabled,
                ocr_min_chars_per_page=settings.ocr_min_chars_per_page,
            )
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

        # Plan K §4: enrichment is gated by the workspace toggle AND a
        # designated utility model — resolved once, up front, so a mid-loop
        # ACL/metadata race (handled below via `still_exists`) can't also
        # flip enrichment eligibility partway through a run.
        workspace = await session.get(Workspace, doc.workspace_id)
        settings = get_settings()
        utility_model = (
            await models_service.resolve_utility_model(session)
            if workspace is not None and workspace.enrichment_enabled
            else None
        )
        completer: LLMCompleter | None = None
        if utility_model is not None:
            completer = LiteLLMStreamer(
                base_url=settings.litellm_url, master_key=settings.litellm_master_key,
            )
        any_batch_enriched = False

        done = 0
        for i in range(0, len(chunks), _BATCH_SIZE):
            batch = chunks[i : i + _BATCH_SIZE]
            dense, sparse = await embed_batch([c.text for c in batch], dense_embedder)

            summaries: list[str | None] = [None] * len(batch)
            if completer is not None and utility_model is not None:
                try:
                    enrichments = [
                        await enrich_chunk(completer, utility_model.litellm_model_name, c.text)
                        for c in batch
                    ]
                    summaries = [e.summary for e in enrichments]
                    sparse_texts = [
                        f"{c.text} {' '.join(e.keywords)}".strip()
                        for c, e in zip(batch, enrichments, strict=True)
                    ]
                    dense, sparse = await embed_batch(
                        [c.text for c in batch], dense_embedder, sparse_texts=sparse_texts
                    )
                    hq_by_chunk = [e.hypothetical_questions for e in enrichments]
                    if any(hq_by_chunk):
                        hq_dense: list[list[list[float]]] = []
                        hq_sparse: list[list[qdrant_models.SparseVector]] = []
                        for qs in hq_by_chunk:
                            if not qs:
                                hq_dense.append([])
                                hq_sparse.append([])
                                continue
                            d, s = await embed_batch(qs, dense_embedder)
                            hq_dense.append(d)
                            hq_sparse.append(s)
                        await upsert_hq_points(
                            org_id=doc.org_id, workspace_id=doc.workspace_id,
                            document_id=doc.id, mime=doc.mime, created_at=doc.created_at,
                            acl_group_ids=[str(g) for g in (doc.acl_group_ids or [])],
                            version=doc.version, meta=doc.meta, is_current=False,
                            parent_chunks=batch, parent_summaries=summaries,
                            hq_texts=hq_by_chunk, hq_dense=hq_dense, hq_sparse=hq_sparse,
                        )
                    any_batch_enriched = True
                except Exception:
                    # Enrichment must never fail ingestion — it's a
                    # searchability enhancement, not a correctness
                    # requirement. Fall back to plain embedding for this
                    # batch; doc.enriched stays False, the honest signal
                    # that enrichment did not actually happen (Task 7's
                    # backfill selector relies on this to retry later).
                    log.warning("chunk_enrichment_failed", exc_info=True)
                    dense, sparse = await embed_batch([c.text for c in batch], dense_embedder)

            await upsert_points(
                org_id=doc.org_id, workspace_id=doc.workspace_id, document_id=doc.id,
                mime=doc.mime, created_at=doc.created_at,
                acl_group_ids=[str(g) for g in (doc.acl_group_ids or [])],
                chunks=batch, dense=dense, sparse=sparse, version=doc.version,
                meta=doc.meta, summaries=summaries,
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
        # Same race, same cure for metadata: a Tags PUT mid-ingest restamps
        # only already-upserted points; later batches carry the stale meta
        # loaded at task start. Re-stamp from the fresh row (final review).
        await update_document_metadata(org_id, document_id, still_exists.meta or {})

        # QUOTA-5: ingestion embedding is attributed, not hidden. TEI reports no
        # token usage, so chars//4 is the documented estimate, flagged by feature.
        await quota_service.record_usage(
            session, org_id=doc.org_id, user_id=doc.created_by, model_id=None,
            feature="ingestion",
            prompt_tokens=sum(len(c.text) for c in chunks) // 4, completion_tokens=0,
        )

        # Stamp on `still_exists`, the freshly-repopulated row (not the
        # possibly-stale `doc` reference), mirroring the ACL/metadata
        # re-stamps above — same identity-map entry as `doc`, but this keeps
        # the intent explicit: `enriched` reflects the CURRENT state, and
        # only flips True once a batch's enrichment genuinely completed.
        if any_batch_enriched:
            still_exists.enriched = True

        doc.status = "indexed"
        doc.vectors_present = True
        await _finish_stage(session, embed_job)
        await _finish_stage(session, upsert_job)
        await documents_service.promote_lineage(session, org_id, doc.lineage_id)


async def run_enrichment_backfill_for_workspace(workspace_id: UUID) -> None:
    """Plan K Task 7 fan-out: one IngestJob(stage="enrich") + one
    run_enrichment_backfill call per current, indexed, not-yet-enriched
    document in the workspace. Triggered only by a genuine
    enrichment_enabled False->True PATCH transition; runs on the `default`
    queue (bulk, non-interactive) so it never blocks a live upload."""
    async with _session() as session:
        docs = list(
            (
                await session.execute(
                    select(Document).where(
                        Document.workspace_id == workspace_id,
                        Document.is_current.is_(True),
                        Document.status == "indexed",
                        Document.enriched.is_(False),
                    )
                )
            ).scalars()
        )
        for doc in docs:
            session.add(IngestJob(document_id=doc.id, stage="enrich"))
        await session.commit()
    for doc in docs:
        await run_enrichment_backfill(doc.id)


async def run_enrichment_backfill(document_id: UUID) -> None:
    """Re-enrich one already-indexed document in place: same deterministic
    point ids as the original ingest (upsert_points/upsert_hq_points), so
    this OVERWRITES the parent points' sparse vector + summary and ADDS hq
    points, without duplicating or re-parsing anything. Guards mirror
    run_embed_upsert's enrichment gate so this is safe to no-op if the
    document is already enriched or preconditions no longer hold by the
    time this runs (e.g. the workspace was toggled back OFF between enqueue
    and execution, or the document was deleted/edited concurrently).
    Ingestion-quota attribution per spec §4."""
    async with _session() as session:
        doc = (
            await session.execute(select(Document).where(Document.id == document_id))
        ).scalar_one_or_none()
        if doc is None or doc.enriched:
            return
        workspace = await session.get(Workspace, doc.workspace_id)
        if workspace is None or not workspace.enrichment_enabled:
            return
        utility_model = await models_service.resolve_utility_model(session)
        if utility_model is None:
            return

        raw = await _storage().get(doc.storage_key + ".chunks.json")
        chunks = [Chunk(**c) for c in json.loads(raw)]
        completer: LLMCompleter = LiteLLMStreamer(
            base_url=get_settings().litellm_url, master_key=get_settings().litellm_master_key,
        )
        dense_embedder = get_dense_embedder()
        total_prompt_tokens = total_completion_tokens = 0

        for i in range(0, len(chunks), _BATCH_SIZE):
            batch = chunks[i : i + _BATCH_SIZE]
            enrichments = [
                await enrich_chunk(completer, utility_model.litellm_model_name, c.text)
                for c in batch
            ]
            summaries = [e.summary for e in enrichments]
            sparse_texts = [
                f"{c.text} {' '.join(e.keywords)}".strip()
                for c, e in zip(batch, enrichments, strict=True)
            ]
            dense, sparse = await embed_batch(
                [c.text for c in batch], dense_embedder, sparse_texts=sparse_texts
            )
            await upsert_points(
                org_id=doc.org_id, workspace_id=doc.workspace_id, document_id=doc.id,
                mime=doc.mime, created_at=doc.created_at,
                acl_group_ids=[str(g) for g in (doc.acl_group_ids or [])],
                chunks=batch, dense=dense, sparse=sparse, version=doc.version,
                meta=doc.meta, is_current=doc.is_current, summaries=summaries,
            )
            hq_by_chunk = [e.hypothetical_questions for e in enrichments]
            if any(hq_by_chunk):
                hq_dense: list[list[list[float]]] = []
                hq_sparse: list[list[qdrant_models.SparseVector]] = []
                for qs in hq_by_chunk:
                    if not qs:
                        hq_dense.append([])
                        hq_sparse.append([])
                        continue
                    d, s = await embed_batch(qs, dense_embedder)
                    hq_dense.append(d)
                    hq_sparse.append(s)
                await upsert_hq_points(
                    org_id=doc.org_id, workspace_id=doc.workspace_id, document_id=doc.id,
                    mime=doc.mime, created_at=doc.created_at,
                    acl_group_ids=[str(g) for g in (doc.acl_group_ids or [])],
                    version=doc.version, meta=doc.meta, is_current=doc.is_current,
                    parent_chunks=batch, parent_summaries=summaries,
                    hq_texts=hq_by_chunk, hq_dense=hq_dense, hq_sparse=hq_sparse,
                )
            total_prompt_tokens += sum(len(c.text) for c in batch) // 4
            total_completion_tokens += sum(len(s or "") for s in summaries) // 4

        doc.enriched = True
        # QUOTA-5 / spec §4: attributed as `feature="ingestion"` usage to the
        # toggling admin's org, same estimation convention as
        # run_embed_upsert's own record_usage call (chars//4, no model token
        # accounting from TEI/the utility model's embedding calls).
        await quota_service.record_usage(
            session, org_id=doc.org_id, user_id=doc.created_by, model_id=utility_model.id,
            feature="ingestion",
            prompt_tokens=total_prompt_tokens, completion_tokens=total_completion_tokens,
        )
        await session.commit()


async def run_delete(document_id: UUID, actor_id: UUID | None) -> None:
    """One task propagating deletion: Qdrant points → MinIO objects → Postgres
    rows, with an audit entry (spec §2.3, DOC-8). Idempotent."""
    async with _session() as session:
        doc = (
            await session.execute(select(Document).where(Document.id == document_id))
        ).scalar_one_or_none()
        if doc is None:
            return
        org_id = doc.org_id  # captured before the row is gone
        lineage_id = doc.lineage_id
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
        # Plan H: deleting the current version leaves the lineage without one
        # until a survivor is promoted (DOC-5).
        await documents_service.promote_lineage(session, org_id, lineage_id)


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
