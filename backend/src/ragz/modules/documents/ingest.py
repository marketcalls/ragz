"""Async ingestion runners: orchestration + job status around the pure pipeline
stages. Called from Celery via asyncio.run (ADR-0001), so each runner owns its
engine lifecycle instead of sharing a loop-bound pool."""

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

from ragz.core.config import get_settings
from ragz.core.db import build_engine, build_session_factory, naive_utc
from ragz.core.storage import ObjectStorage, build_storage
from ragz.modules.audit.service import record_audit
from ragz.modules.chat.llm import LiteLLMStreamer, LLMCompleter
from ragz.modules.documents import service as documents_service
from ragz.modules.documents.enrichment import enrich_chunk
from ragz.modules.documents.models import Document, IngestJob
from ragz.modules.documents.parsers import parse_document
from ragz.modules.documents.pipeline import (
    Chunk,
    IngestFailure,
    PageBlock,
    chunk_document,
    embed_batch,
    upsert_hq_points,
    upsert_points,
)
from ragz.modules.models import service as models_service
from ragz.modules.quotas import service as quota_service
from ragz.modules.retrieval.embeddings import get_dense_embedder
from ragz.modules.retrieval.service import (
    delete_document_points,
    delete_workspace_points,
    ensure_collection,
    resolve_collection_name,
    update_document_acl,
    update_document_metadata,
)
from ragz.modules.tenancy.models import Workspace
from ragz.modules.tenancy.reembed_models import ReembedJob

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
            blocks = await parse_document(
                session, settings, data=data, filename=doc.filename
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
        # Chunk-methods plan Task 2: a document override wins over the
        # workspace default; both fall back to "heading" (byte-identical to
        # the pre-Task-2 chunk_blocks call) via chunk_document's dispatch.
        workspace = await session.get(Workspace, doc.workspace_id)
        assert workspace is not None  # doc.workspace_id is a non-nullable FK
        method = doc.chunk_method_override or workspace.chunk_method
        chunks = chunk_document(blocks, method=method)
        if not chunks:
            await _fail(session, doc, job, "chunking produced no chunks")
            raise IngestFailure("chunking produced no chunks")
        await storage.put(
            doc.storage_key + ".chunks.json",
            json.dumps([asdict(c) for c in chunks]).encode(),
            content_type="application/json",
        )
        await _finish_stage(session, job)


async def run_embed_upsert(document_id: UUID) -> UUID | None:
    """Stages 3+4 in one runner: embedding a batch and upserting it immediately
    avoids persisting vectors between tasks; ingest_jobs still shows both stages.

    run_delete is idempotent and doesn't coordinate with in-flight ingest, so it
    can win the race either before this runner starts or partway through it.
    Both must be handled without raising and without leaving retrievable points.

    Returns promote_lineage's needs-reindex document id (or None): this runner
    never imports worker.tasks itself (Plan K Task 11) -- the Celery task
    wrapper that calls it (worker/tasks.py's embed_upsert_task/reindex_task,
    which already define enqueue_reindex in the same module) performs the
    actual enqueue."""
    async with _session() as session:
        doc = (
            await session.execute(select(Document).where(Document.id == document_id))
        ).scalar_one_or_none()
        if doc is None:
            return None  # deleted before we started: nothing was written, nothing to clean up
        org_id = doc.org_id  # captured now: doc may be gone by the time we re-check below
        # A re-index enters HERE, not at run_parse, so nothing else would move
        # the row off its previous status: a retried failure kept showing
        # "failed" for the whole (succeeding) run, and re-indexing a healthy
        # doc showed no in-flight state at all. _start_stage commits below, so
        # this rides along on that same commit and is visible immediately.
        doc.status = "processing"
        doc.error = None
        embed_job = await _start_stage(session, document_id, "embed")
        upsert_job = await _start_stage(session, document_id, "upsert")
        raw = await _storage().get(doc.storage_key + ".chunks.json")
        chunks = [Chunk(**c) for c in json.loads(raw)]

        # DOC-10: loaded here (was loaded further down, after ensure_collection)
        # so the workspace's ACTUAL embedding model drives both collection
        # setup and the embedder -- was ensure_collection() with no args at
        # all, silently always the seeded default regardless of ws.embedding_model_id.
        workspace = await session.get(Workspace, doc.workspace_id)
        assert workspace is not None  # doc.workspace_id is a non-nullable FK
        embedding_model = await models_service.get_model(session, workspace.embedding_model_id)
        collection_name = embedding_model.collection_name
        assert collection_name is not None
        await ensure_collection(collection_name, embedding_model.dimension)  # type: ignore[arg-type]
        dense_embedder = get_dense_embedder(
            embedding_model.id, provider_kind=embedding_model.provider_kind,
            litellm_model_name=embedding_model.litellm_model_name,
        )

        # Plan K §4: enrichment is gated by the workspace toggle AND a
        # designated utility model — resolved once, up front, so a mid-loop
        # ACL/metadata race (handled below via `still_exists`) can't also
        # flip enrichment eligibility partway through a run.
        settings = get_settings()
        utility_model = (
            await models_service.resolve_utility_model(session)
            if workspace.enrichment_enabled
            else None
        )
        completer: LLMCompleter | None = None
        if utility_model is not None:
            completer = LiteLLMStreamer(
                base_url=settings.litellm_url, master_key=settings.litellm_master_key,
            )
        any_batch_enriched = False

        # Cost reporting (design 2026-08-15 §2): billed dense-embedding tokens
        # per performed call are appended here (hosted providers only; TEI
        # reports 0). Summed into ONE embedding usage record per document run
        # below -- one row, not one per batch, to stay cheap.
        embed_token_sink: list[int] = []
        done = 0
        for i in range(0, len(chunks), _BATCH_SIZE):
            batch = chunks[i : i + _BATCH_SIZE]
            dense, sparse = await embed_batch(
                [c.text for c in batch], dense_embedder, usage_sink=embed_token_sink
            )

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
                        [c.text for c in batch], dense_embedder,
                        sparse_texts=sparse_texts, usage_sink=embed_token_sink,
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
                            d, s = await embed_batch(
                                qs, dense_embedder, usage_sink=embed_token_sink
                            )
                            hq_dense.append(d)
                            hq_sparse.append(s)
                        await upsert_hq_points(
                            org_id=doc.org_id, workspace_id=doc.workspace_id,
                            document_id=doc.id, mime=doc.mime, created_at=doc.created_at,
                            acl_group_ids=[str(g) for g in (doc.acl_group_ids or [])],
                            version=doc.version, meta=doc.meta, is_current=False,
                            parent_chunks=batch, parent_summaries=summaries,
                            hq_texts=hq_by_chunk, hq_dense=hq_dense, hq_sparse=hq_sparse,
                            collection_name=collection_name,
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
                    dense, sparse = await embed_batch(
                        [c.text for c in batch], dense_embedder, usage_sink=embed_token_sink
                    )

            await upsert_points(
                org_id=doc.org_id, workspace_id=doc.workspace_id, document_id=doc.id,
                mime=doc.mime, created_at=doc.created_at,
                acl_group_ids=[str(g) for g in (doc.acl_group_ids or [])],
                chunks=batch, dense=dense, sparse=sparse, version=doc.version,
                meta=doc.meta, summaries=summaries, collection_name=collection_name,
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
            await delete_document_points(org_id, document_id, collection_name=collection_name)
            return None

        # An ACL admin PUT racing mid-ingest may have stamped only the points
        # upserted so far (update_document_acl re-stamps existing points, not
        # ones written by later batches) — or landed between the last batch's
        # upsert and here. Re-stamp against the row's CURRENT acl_group_ids
        # (freshly repopulated above) so every point for this document
        # reflects the latest PG state before the document is marked indexed
        # and becomes retrievable.
        await update_document_acl(
            org_id, document_id, still_exists.acl_group_ids, collection_name=collection_name
        )
        # Same race, same cure for metadata: a Tags PUT mid-ingest restamps
        # only already-upserted points; later batches carry the stale meta
        # loaded at task start. Re-stamp from the fresh row (final review).
        await update_document_metadata(
            org_id, document_id, still_exists.meta or {}, collection_name=collection_name
        )

        # Cost reporting (design 2026-08-15 §2): a hosted embedder's ACTUAL
        # billed tokens for this doc run, attributed to the workspace embedding
        # model so reporting can price it off model_catalog cost/token. TEI is
        # self-hosted -> sink sums to 0 -> free, skip the row. commit=False
        # rides the ingestion record's commit just below (one commit, not two).
        embed_tokens = sum(embed_token_sink)
        if embed_tokens > 0:
            await quota_service.record_usage(
                session, org_id=doc.org_id, user_id=doc.created_by,
                workspace_id=doc.workspace_id,
                model_id=embedding_model.id, feature="embedding",
                prompt_tokens=embed_tokens, completion_tokens=0, commit=False,
            )

        # QUOTA-5: ingestion embedding is attributed, not hidden. TEI reports no
        # token usage, so chars//4 is the documented estimate, flagged by feature.
        await quota_service.record_usage(
            session, org_id=doc.org_id, user_id=doc.created_by,
            workspace_id=doc.workspace_id, model_id=None,
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
        return await documents_service.promote_lineage(session, org_id, doc.lineage_id)


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
        embedding_model = await models_service.get_model(session, workspace.embedding_model_id)
        collection_name = embedding_model.collection_name
        assert collection_name is not None

        raw = await _storage().get(doc.storage_key + ".chunks.json")
        chunks = [Chunk(**c) for c in json.loads(raw)]
        completer: LLMCompleter = LiteLLMStreamer(
            base_url=get_settings().litellm_url, master_key=get_settings().litellm_master_key,
        )
        dense_embedder = get_dense_embedder(
            embedding_model.id, provider_kind=embedding_model.provider_kind,
            litellm_model_name=embedding_model.litellm_model_name,
        )
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
                collection_name=collection_name,
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
                    collection_name=collection_name,
                )
            total_prompt_tokens += sum(len(c.text) for c in batch) // 4
            total_completion_tokens += sum(len(s or "") for s in summaries) // 4

        doc.enriched = True
        # QUOTA-5 / spec §4: attributed as `feature="ingestion"` usage to the
        # toggling admin's org, same estimation convention as
        # run_embed_upsert's own record_usage call (chars//4, no model token
        # accounting from TEI/the utility model's embedding calls).
        await quota_service.record_usage(
            session, org_id=doc.org_id, user_id=doc.created_by,
            workspace_id=doc.workspace_id, model_id=utility_model.id,
            feature="ingestion",
            prompt_tokens=total_prompt_tokens, completion_tokens=total_completion_tokens,
        )
        await session.commit()


async def run_reembed_workspace(
    workspace_id: UUID, job_id: UUID, new_embedding_model_id: UUID
) -> None:
    """DOC-10: re-embed every current, indexed document in a workspace into a
    NEW embedding model's collection, using each document's already-stored
    chunks.json (never re-parses or re-chunks — same reuse
    worker/tasks.py::reindex_task already relies on for single-document
    re-embeds). On completion, deletes the workspace's vectors from the OLD
    collection (workspace-scoped, via the existing _tenant_filter -- NOT a
    whole-collection wipe, since other workspaces may still share it) and
    flips workspace.embedding_model_id.

    Fix round 2: job_id identifies the ReembedJob row that
    api/routes/workspaces.py::start_reembed already created SYNCHRONOUSLY
    (started_at set) in its own request transaction, BEFORE enqueueing the
    Celery task that runs this function -- this runner updates that existing
    row (documents_total/documents_done/error/finished_at) instead of
    creating one of its own. That closes the window where
    documents/service.py::create_from_upload's in-progress guard would see
    no ReembedJob at all between the route returning 202 and Celery actually
    picking up the task (see .superpowers/sdd/final-review-fix-report.md).

    Failure mid-loop (any batch's embed/upsert raises): job.error is recorded
    and the exception re-raised BEFORE the old collection is touched and
    BEFORE workspace.embedding_model_id is flipped -- the workspace is left
    pointing at its OLD (still fully populated) collection rather than a new
    one that only some documents made it into.

    Fix round 3: the failure handling now wraps EVERYTHING from here through
    the end of the run, not just the per-document loop. The prior version
    only wrapped the loop -- so a failure in the SETUP phase (old/new model
    lookup via models_service.get_model, which raises NotFoundError if a
    model was deleted mid-flight; the collection-name assertion;
    ensure_collection, a real Qdrant call that can transiently fail; or the
    documents query/count commit) propagated straight past this function,
    through Celery's retry wrapper, to IngestTask.on_failure ->
    ingest.mark_failed(workspace_id, ...) -- which looks for a Document with
    that id, finds none (it's a workspace_id), and silently no-ops. The
    ReembedJob row was left with started_at set and finished_at NULL
    forever, and since create_from_upload's guard checks for exactly that
    state, every future upload to the workspace was permanently rejected.
    Now any exception anywhere in this block stamps job.error/finished_at
    before propagating."""
    async with _session() as session:
        workspace = await session.get(Workspace, workspace_id)
        job = await session.get(ReembedJob, job_id)
        if workspace is None:
            # Workspace vanished between start_reembed creating this job and
            # Celery picking up the task (e.g. deleted meanwhile). Close the
            # job anyway -- otherwise it stays "in progress" forever and the
            # create_from_upload guard would (harmlessly, since the
            # workspace is gone, but pointlessly) never clear for it.
            if job is not None:
                job.error = "workspace no longer exists"
                job.finished_at = naive_utc()
                await session.commit()
            return
        if job is None:
            # Defensive: start_reembed creates this row synchronously in the
            # same request that enqueues this task, so it should always
            # exist by the time Celery runs it. Nothing safe to update
            # without a job row to report into.
            log.warning(
                "reembed_job_missing", workspace_id=str(workspace_id), job_id=str(job_id)
            )
            return

        try:
            old_model = await models_service.get_model(session, workspace.embedding_model_id)
            new_model = await models_service.get_model(session, new_embedding_model_id)
            if old_model.id == new_model.id:
                # Defensive no-op: start_reembed already rejects a same-model
                # request with a 409 before this task is ever enqueued, so
                # this should only trigger if the task is invoked directly
                # (e.g. a replayed Celery message bypassing the route).
                # Without this guard, old_collection == new_collection below,
                # and the workspace-scoped delete from the "OLD" collection
                # after the upsert loop would delete every point this same
                # run just wrote -- silently wiping the workspace's vectors.
                # The job still must be closed here (finished_at stamped) --
                # otherwise it stays "in progress" forever and
                # create_from_upload's guard would permanently block uploads
                # to this workspace.
                log.warning(
                    "reembed_noop_same_model",
                    workspace_id=str(workspace_id),
                    model_id=str(new_model.id),
                )
                job.finished_at = naive_utc()
                await session.commit()
                return
            old_collection = old_model.collection_name
            new_collection = new_model.collection_name
            assert old_collection is not None and new_collection is not None
            await ensure_collection(new_collection, new_model.dimension)  # type: ignore[arg-type]
            new_embedder = get_dense_embedder(
                new_model.id, provider_kind=new_model.provider_kind,
                litellm_model_name=new_model.litellm_model_name,
            )

            docs = list(
                (
                    await session.execute(
                        select(Document).where(
                            Document.workspace_id == workspace_id,
                            Document.is_current.is_(True),
                            Document.status == "indexed",
                        )
                    )
                ).scalars()
            )
            job.documents_total = len(docs)
            await session.commit()

            for doc in docs:
                raw = await _storage().get(doc.storage_key + ".chunks.json")
                chunks = [Chunk(**c) for c in json.loads(raw)]
                for i in range(0, len(chunks), _BATCH_SIZE):
                    batch = chunks[i : i + _BATCH_SIZE]
                    dense, sparse = await embed_batch([c.text for c in batch], new_embedder)
                    await upsert_points(
                        org_id=doc.org_id, workspace_id=doc.workspace_id, document_id=doc.id,
                        mime=doc.mime, created_at=doc.created_at,
                        acl_group_ids=[str(g) for g in (doc.acl_group_ids or [])],
                        chunks=batch, dense=dense, sparse=sparse, version=doc.version,
                        meta=doc.meta, is_current=True, collection_name=new_collection,
                    )
                job.documents_done += 1
                await session.commit()

            # Workspace-scoped delete from the OLD collection -- other
            # workspaces may still share that collection, so this is never a
            # whole-collection wipe. Only reached once every document above
            # has been re-embedded into new_collection without error.
            await delete_workspace_points(
                workspace.org_id, workspace_id, collection_name=old_collection
            )

            workspace.embedding_model_id = new_model.id
            job.finished_at = naive_utc()
            await session.commit()
        except Exception as exc:
            job.error = str(exc)[:1000]
            job.finished_at = naive_utc()
            await session.commit()
            raise


async def run_delete(document_id: UUID, actor_id: UUID | None) -> UUID | None:
    """One task propagating deletion: Qdrant points → MinIO objects → Postgres
    rows, with an audit entry (spec §2.3, DOC-8). Idempotent.

    Returns promote_lineage's needs-reindex document id (or None) -- see
    run_embed_upsert's docstring; worker/tasks.py's delete_task performs the
    actual enqueue."""
    async with _session() as session:
        doc = (
            await session.execute(select(Document).where(Document.id == document_id))
        ).scalar_one_or_none()
        if doc is None:
            return None
        org_id = doc.org_id  # captured before the row is gone
        lineage_id = doc.lineage_id
        collection_name = await resolve_collection_name(session, doc.workspace_id)
        await delete_document_points(doc.org_id, document_id, collection_name=collection_name)
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
        return await documents_service.promote_lineage(session, org_id, lineage_id)


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


async def reconcile_security_projections(limit: int = 500) -> int:
    """Re-drive documents whose committed security state never reached Qdrant.

    Fail-closed ACL projection (review P0) trades availability for safety: a
    document with an unprojected revision is excluded from retrieval. That is
    the correct default, but without this it is also PERMANENT -- one Qdrant
    blip would leave a document invisible until someone noticed and re-saved
    its ACL by hand. This is the other half of the contract: the system closes
    the door on failure, then reopens it by itself once the store is reachable.

    Idempotent and safe to run concurrently with live traffic: it re-reads the
    current Postgres ACL and projects that, so a document that has since been
    changed again is simply projected at its newer revision.

    Returns the number of documents brought back to 'active', so the beat log
    (and, later, a metric) shows whether the backlog is draining or growing.
    """
    from ragz.modules.documents.service import project_document_security

    recovered = 0
    async with _session() as session:
        stale = (
            (
                await session.execute(
                    select(Document)
                    .where(
                        # Both halves matter. index_state catches the ordinary
                        # failure; the revision comparison catches a row that
                        # says "active" while its projection is behind -- which
                        # a state-only query would skip forever, leaving a stale
                        # ACL served indefinitely. The compare-and-set in
                        # project_document_security should make that
                        # unreachable, and the DB constraint makes it
                        # impossible, but the sweep must not DEPEND on either
                        # being perfect: this is the backstop.
                        (Document.index_state != "active")
                        | (
                            Document.projected_security_revision
                            != Document.security_revision
                        )
                    )
                    .order_by(Document.updated_at)
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
        for doc in stale:
            try:
                await project_document_security(session, doc)
            except Exception:  # noqa: BLE001 - one bad document must not stall the sweep
                log.warning(
                    "security_projection_reconcile_failed",
                    document_id=str(doc.id),
                    security_revision=doc.security_revision,
                )
                continue
            recovered += 1
    if stale:
        log.info(
            "security_projection_reconciled",
            attempted=len(stale),
            recovered=recovered,
            remaining=len(stale) - recovered,
        )
    return recovered
