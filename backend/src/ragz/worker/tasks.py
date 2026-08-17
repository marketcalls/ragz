"""Thin sync wrappers over async runners (ADR-0001: asyncio.run per task).

No business logic lives here — only retry/queue/failure plumbing.
"""

import asyncio
from datetime import timedelta
from typing import Any
from uuid import UUID

import structlog
from celery import Task, chain

from ragz.core.config import get_settings
from ragz.core.db import build_engine, build_session_factory, naive_utc
from ragz.core.storage import build_storage
from ragz.modules.chat import service as chat_service
from ragz.modules.chat.attachments import extract_text
from ragz.modules.chat.llm import LiteLLMStreamer
from ragz.modules.chat.models import ChatAttachment
from ragz.modules.documents import ingest
from ragz.modules.documents.pipeline import IngestFailure
from ragz.modules.evals.runner import run_eval
from ragz.modules.evals.service import workspace_ids_with_golden_queries
from ragz.modules.models import catalog
from ragz.modules.retrieval.service import delete_ephemeral_points, retrieve
from ragz.modules.tenancy.models import Workspace
from ragz.worker.celery_app import celery_app

_MAX_RETRIES = 3


class IngestTask(Task):
    """Marks the document failed once retries are exhausted (or on IngestFailure)."""

    def on_failure(self, exc: Exception, task_id: str, args: tuple[Any, ...],
                   kwargs: dict[str, Any], einfo: Any) -> None:
        # Beat-scheduled maintenance tasks (outbox dispatch, the reconcilers)
        # share this base but take NO arguments, so args[0] raised IndexError
        # and replaced the real failure with a confusing one from the hook
        # itself. Nothing to mark in that case -- let the original propagate.
        if not args:
            structlog.get_logger().error(
                "scheduled_task_failed", task=self.name, error=str(exc)
            )
            return
        asyncio.run(ingest.mark_failed(UUID(str(args[0])), str(exc)))


class DeleteTask(Task):
    """Without this, an exhausted delete retry fails silently: the document
    sits forever in the "deleting" status set by the route with no error and
    no way for the UI to tell the user anything went wrong."""

    def on_failure(self, exc: Exception, task_id: str, args: tuple[Any, ...],
                   kwargs: dict[str, Any], einfo: Any) -> None:
        asyncio.run(ingest.mark_failed(UUID(str(args[0])), f"delete failed: {exc}"))


def _run(self: Task, coro_factory: Any) -> Any:
    try:
        return asyncio.run(coro_factory())
    except IngestFailure:
        raise  # terminal: already recorded on the document; stops the chain, no retry
    except Exception as exc:
        raise self.retry(exc=exc, countdown=2 ** self.request.retries) from exc


@celery_app.task(base=IngestTask, bind=True, max_retries=_MAX_RETRIES, name="documents.parse")
def parse_task(self: Task, document_id: str) -> str:
    _run(self, lambda: ingest.run_parse(UUID(document_id)))
    return document_id


@celery_app.task(base=IngestTask, bind=True, max_retries=_MAX_RETRIES, name="documents.chunk")
def chunk_task(self: Task, document_id: str) -> str:
    _run(self, lambda: ingest.run_chunk(UUID(document_id)))
    return document_id


@celery_app.task(base=IngestTask, bind=True, max_retries=_MAX_RETRIES,
                 name="documents.embed_upsert")
def embed_upsert_task(self: Task, document_id: str) -> str:
    # Plan K Task 11: run_embed_upsert returns promote_lineage's needs-reindex
    # id instead of enqueueing itself (modules/ must never import worker/) --
    # this is the entrypoint that performs the actual enqueue, using the
    # enqueue_reindex defined right here in the same module.
    needs_reindex = _run(self, lambda: ingest.run_embed_upsert(UUID(document_id)))
    if needs_reindex is not None:
        enqueue_reindex(needs_reindex)
    return document_id


@celery_app.task(base=DeleteTask, bind=True, max_retries=_MAX_RETRIES, name="documents.delete")
def delete_task(self: Task, document_id: str, actor_id: str | None = None) -> None:
    try:
        # Plan K Task 11: same inversion as embed_upsert_task above -- run_delete
        # returns the needs-reindex id, this entrypoint enqueues it.
        needs_reindex = asyncio.run(ingest.run_delete(UUID(document_id),
                                                       UUID(actor_id) if actor_id else None))
        if needs_reindex is not None:
            enqueue_reindex(needs_reindex)
    except Exception as exc:
        raise self.retry(exc=exc, countdown=2 ** self.request.retries) from exc


@celery_app.task(base=IngestTask, bind=True, max_retries=_MAX_RETRIES, name="documents.reindex")
def reindex_task(self: Task, document_id: str) -> str:
    """Re-run embed+upsert from stored chunks.json (deterministic point ids
    make this idempotent) when promote_lineage picks a winner whose points
    were previously deleted (DOC-5). Its tail re-runs promote_lineage, which
    may again return a (different) needs-reindex id if the newly-effective
    winner also lacks points -- same entrypoint-enqueues pattern as above."""
    needs_reindex = _run(self, lambda: ingest.run_embed_upsert(UUID(document_id)))
    if needs_reindex is not None:
        enqueue_reindex(needs_reindex)
    return document_id


@celery_app.task(base=IngestTask, bind=True, max_retries=_MAX_RETRIES,
                 name="documents.enrich_backfill")
def enrich_backfill_task(self: Task, workspace_id: str) -> str:
    """Plan K Task 7: fan-out to every current/indexed/not-yet-enriched
    document in the workspace, triggered by the PATCH route on a genuine
    enrichment_enabled False->True transition."""
    _run(self, lambda: ingest.run_enrichment_backfill_for_workspace(UUID(workspace_id)))
    return workspace_id


def select_queue(size_bytes: int) -> str:
    """Uploads under the interactive threshold jump the bulk queue (spec §3.2)."""
    limit = get_settings().interactive_upload_mb * 1024 * 1024
    return "interactive" if size_bytes < limit else "default"


def build_ingest_chain(document_id: str, queue: str) -> Any:
    return chain(
        parse_task.si(document_id).set(queue=queue),
        chunk_task.si(document_id).set(queue=queue),
        embed_upsert_task.si(document_id).set(queue=queue),
    )


def enqueue_ingest(document_id: UUID, size_bytes: int) -> None:
    build_ingest_chain(str(document_id), select_queue(size_bytes)).apply_async()


def enqueue_delete(document_id: UUID, actor_id: UUID) -> None:
    delete_task.si(str(document_id), str(actor_id)).apply_async(queue="interactive")


def enqueue_reindex(document_id: UUID) -> None:
    reindex_task.si(str(document_id)).apply_async(queue="interactive")


def enqueue_enrichment_backfill(workspace_id: UUID) -> None:
    """Bulk background work (default queue) — never the interactive queue,
    so a toggle-ON never blocks a live upload (Plan K Task 7)."""
    enrich_backfill_task.si(str(workspace_id)).apply_async(queue="default")


@celery_app.task(base=IngestTask, bind=True, max_retries=_MAX_RETRIES, name="workspaces.reembed")
def reembed_workspace_task(
    self: Task, workspace_id: str, job_id: str, new_embedding_model_id: str
) -> str:
    """DOC-10: fan-out re-embed of every document in a workspace into a new
    embedding model's collection. base=IngestTask is a reasonable reuse here
    -- on exhausted retries it calls ingest.mark_failed(args[0], ...), which
    expects a document_id; workspace_id isn't a document_id, so this task
    intentionally does NOT rely on that on_failure hook for user-facing
    status (ReembedJob.error is set directly inside run_reembed_workspace's
    own except block before it re-raises for Celery's retry to see).

    Fix round 2: job_id identifies the ReembedJob row start_reembed already
    created SYNCHRONOUSLY (with started_at set) in its own request
    transaction, before this task was ever enqueued -- closing the race
    where create_from_upload's in-progress guard saw no job during the
    enqueue-to-Celery-pickup gap. This task updates that existing row rather
    than creating a new one."""
    _run(
        self,
        lambda: ingest.run_reembed_workspace(
            UUID(workspace_id), UUID(job_id), UUID(new_embedding_model_id)
        ),
    )
    return workspace_id


def enqueue_reembed_workspace(
    workspace_id: UUID, job_id: UUID, new_embedding_model_id: UUID
) -> None:
    """Bulk background work (default queue), same posture as
    enqueue_enrichment_backfill -- never blocks a live request. job_id is the
    ReembedJob row the caller (start_reembed) already created synchronously."""
    reembed_workspace_task.si(
        str(workspace_id), str(job_id), str(new_embedding_model_id)
    ).apply_async(queue="default")


@celery_app.task(name="chat.audit_message")
def audit_message_task(message_id: str) -> None:
    """Phase 3 Auditor (§3): async, best-effort. Failures are logged, never
    retried with backoff -- a missed audit is an observability gap, not a
    user-facing incident (unlike ingestion, which retries)."""

    async def _run() -> None:
        settings = get_settings()
        engine = build_engine(settings.database_url)
        try:
            factory = build_session_factory(engine)
            async with factory() as session:
                await chat_service.audit_message(session, UUID(message_id))
        finally:
            await engine.dispose()

    try:
        asyncio.run(_run())
    except Exception:
        structlog.get_logger().warning("audit_message_failed", message_id=message_id, exc_info=True)


def enqueue_audit_message(message_id: UUID) -> None:
    audit_message_task.si(str(message_id)).apply_async(queue="default")


async def _mark_attachment_failed_best_effort(attachment_id: str) -> None:
    """Separate engine/session from the outer-failure path below -- the
    attachment/session used by `_run` may itself be the thing that broke, so
    this gets a fresh connection rather than reusing anything from `_run`."""
    settings = get_settings()
    engine = build_engine(settings.database_url)
    try:
        factory = build_session_factory(engine)
        async with factory() as session:
            await chat_service.mark_attachment_failed(session, UUID(attachment_id))
    finally:
        await engine.dispose()


@celery_app.task(name="attachments.process")
def process_attachment_task(attachment_id: str) -> None:
    """Best-effort text extraction for a chat attachment (Task 2, DOC-9).
    No retry base class (unlike the IngestTask/DeleteTask above) — a failed
    extraction degrades gracefully to status="failed" rather than needing a
    retry, mirroring audit_message_task's fire-and-forget pattern."""

    async def _run() -> None:
        settings = get_settings()
        engine = build_engine(settings.database_url)
        try:
            factory = build_session_factory(engine)
            async with factory() as session:
                attachment = await session.get(ChatAttachment, UUID(attachment_id))
                if attachment is None:
                    return
                await chat_service.mark_attachment_processing(session, attachment.id)
                try:
                    storage = build_storage(settings)
                    data = await storage.get(attachment.storage_key)
                    text = await asyncio.to_thread(
                        extract_text, data, attachment.filename
                    )
                    await chat_service.mark_attachment_ready(session, attachment.id, text)
                except Exception:
                    structlog.get_logger().warning(
                        "attachment_processing_failed", exc_info=True
                    )
                    await chat_service.mark_attachment_failed(session, attachment.id)
        finally:
            await engine.dispose()

    try:
        asyncio.run(_run())
    except Exception:
        # Review fix (DOC-9 Task 2): mirror audit_message_task's outer guard
        # exactly -- nothing may escape this task, or a transient DB error
        # anywhere in `_run` (e.g. session.get, mark_attachment_processing's
        # commit) leaves the attachment stuck in "queued"/"processing"
        # forever with only a bare Celery task-failure record. Best-effort
        # flip the status to "failed" too; if the DB itself is unreachable
        # that write will also fail, which is an acceptable rare
        # double-failure, not something to solve further here.
        structlog.get_logger().warning(
            "attachment_processing_outer_failed", attachment_id=attachment_id, exc_info=True
        )
        try:
            asyncio.run(_mark_attachment_failed_best_effort(attachment_id))
        except Exception:
            # Best-effort write on top of a best-effort write: the DB itself
            # is likely unreachable here. Rare double-failure, not solved
            # further per the review finding -- just log and swallow.
            structlog.get_logger().warning(
                "attachment_processing_failed_status_write_failed",
                attachment_id=attachment_id, exc_info=True,
            )


def enqueue_attachment_processing(attachment_id: UUID) -> None:
    process_attachment_task.si(str(attachment_id)).apply_async(queue="interactive")


@celery_app.task(name="evals.run")
def run_eval_task(workspace_id: str, triggered_by: str) -> None:
    """Minimal trigger for Task 11 (the admin on-demand button); Task 12 adds
    the nightly/settings-change triggers alongside this same task."""

    async def _run() -> None:
        settings = get_settings()
        engine = build_engine(settings.database_url)
        try:
            factory = build_session_factory(engine)
            async with factory() as session:
                ws = await session.get(Workspace, UUID(workspace_id))
                if ws is None:
                    return
                completer = LiteLLMStreamer(
                    base_url=settings.litellm_url, master_key=settings.litellm_master_key
                )
                await run_eval(
                    session, ws, triggered_by=triggered_by, retriever=retrieve,
                    completer=completer,
                )
        finally:
            await engine.dispose()

    asyncio.run(_run())


def enqueue_eval_run(workspace_id: UUID, triggered_by: str) -> None:
    run_eval_task.si(str(workspace_id), triggered_by).apply_async(queue="default")


@celery_app.task(name="evals.run_all_workspaces")
def run_all_workspaces_task() -> None:
    """Nightly fan-out (Task 12, §6; Plan G Task 12 precedent: interval-based,
    not true crontab). Only workspaces with >=1 golden query get a run -
    most workspaces will never author any, and a run over zero queries is a
    wasted DB write."""

    async def _run() -> None:
        settings = get_settings()
        engine = build_engine(settings.database_url)
        try:
            factory = build_session_factory(engine)
            async with factory() as session:
                for workspace_id in await workspace_ids_with_golden_queries(session):
                    enqueue_eval_run(workspace_id, "nightly")
        finally:
            await engine.dispose()

    asyncio.run(_run())


@celery_app.task(name="attachments.cleanup_stale")
def cleanup_stale_attachments_task() -> None:
    """Task 7 (DOC-9): daily Beat sweep deleting ephemeral chat attachments
    past the 24h TTL -- DB row (chat_service.delete_attachment), MinIO blob
    (storage.delete), and Qdrant points (delete_ephemeral_points). Whole-branch
    review fix: points are deleted per chat_id AND scoped to that chat's
    SPECIFIC stale attachment ids (grouped below), not the whole chat -- a
    chat can hold a mix of a >24h attachment (swept here) and a fresh (<24h)
    retrieval-routed attachment, and the fresh one's vectors must survive
    until its OWN 24h TTL. A blob-delete failure is logged and swallowed
    rather than aborting the sweep -- an orphaned MinIO object is a cheap,
    recoverable leak, not a reason to leave the DB row (and the Qdrant points
    still referencing it) around for another day."""

    async def _run() -> None:
        settings = get_settings()
        engine = build_engine(settings.database_url)
        try:
            factory = build_session_factory(engine)
            async with factory() as session:
                cutoff = naive_utc() - timedelta(hours=24)
                stale = await chat_service.list_stale_attachments(session, cutoff)
                stale_ids_by_chat: dict[UUID, list[UUID]] = {}
                for a in stale:
                    stale_ids_by_chat.setdefault(a.chat_id, []).append(a.id)
                storage = build_storage(settings)
                for attachment in stale:
                    try:
                        await storage.delete(attachment.storage_key)
                    except Exception:
                        structlog.get_logger().warning(
                            "attachment_blob_delete_failed",
                            attachment_id=str(attachment.id), exc_info=True,
                        )
                    await chat_service.delete_attachment(session, attachment)
                for chat_id, attachment_ids in stale_ids_by_chat.items():
                    await delete_ephemeral_points(chat_id, attachment_ids)
        finally:
            await engine.dispose()

    asyncio.run(_run())


@celery_app.task(name="models.refresh_catalog")
def refresh_model_catalog() -> None:
    """Beat-scheduled daily; refresh_catalog's own 3-day cache makes redundant
    runs a cheap no-op (MODEL-10/G7)."""

    async def _run() -> None:
        settings = get_settings()
        engine = build_engine(settings.database_url)
        try:
            factory = build_session_factory(engine)
            async with factory() as session:
                n = await catalog.refresh_catalog(session, settings)
                structlog.get_logger().info("model_catalog_refreshed", upserted=n)
        finally:
            await engine.dispose()

    asyncio.run(_run())


@celery_app.task(base=IngestTask, bind=True, max_retries=_MAX_RETRIES,
                 name="documents.reconcile_security_projections")
def reconcile_security_projections_task(self: Task) -> int:
    """Re-drive documents whose committed ACL never reached Qdrant (review P0).

    Fail-closed projection makes a Qdrant failure hide a document instead of
    over-sharing it. This is what makes that recoverable without a human:
    without it, one blip would leave the document invisible until someone
    re-saved its ACL by hand.
    """
    return _run(self, lambda: ingest.reconcile_security_projections())  # type: ignore[no-any-return]


@celery_app.task(base=IngestTask, bind=True, max_retries=_MAX_RETRIES,
                 name="outbox.dispatch_pending")
def outbox_dispatch_task(self: Task) -> int:
    """Sweep undispatched outbox events (review P1).

    The API nudges the dispatcher inline after a commit, so this sweep is the
    SAFETY NET, not the normal path: it covers the cases the nudge cannot --
    the process dying between commit and nudge, the broker being down at that
    moment, or an event published by something with no request to nudge from.
    """
    from ragz.worker.outbox import dispatch_pending

    return _run(self, lambda: dispatch_pending())  # type: ignore[no-any-return]


@celery_app.task(base=IngestTask, bind=True, max_retries=_MAX_RETRIES,
                 name="documents.reconcile_stuck")
def reconcile_stuck_documents_task(self: Task) -> dict[str, int]:
    """Re-drive documents stranded mid-pipeline (review P1).

    The outbox stops work being lost from now on; this recovers rows stranded
    BEFORE it existed, and any whose worker died after claiming the message.
    """
    return _run(self, lambda: ingest.reconcile_stuck_documents())  # type: ignore[no-any-return]
