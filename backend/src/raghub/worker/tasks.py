"""Thin sync wrappers over async runners (ADR-0001: asyncio.run per task).

No business logic lives here — only retry/queue/failure plumbing.
"""

import asyncio
from typing import Any
from uuid import UUID

import structlog
from celery import Task, chain

from raghub.core.config import get_settings
from raghub.core.db import build_engine, build_session_factory
from raghub.modules.chat import service as chat_service
from raghub.modules.chat.llm import LiteLLMStreamer
from raghub.modules.documents import ingest
from raghub.modules.documents.pipeline import IngestFailure
from raghub.modules.evals.runner import run_eval
from raghub.modules.models import catalog
from raghub.modules.retrieval.service import retrieve
from raghub.modules.tenancy.models import Workspace
from raghub.worker.celery_app import celery_app

_MAX_RETRIES = 3


class IngestTask(Task):
    """Marks the document failed once retries are exhausted (or on IngestFailure)."""

    def on_failure(self, exc: Exception, task_id: str, args: tuple[Any, ...],
                   kwargs: dict[str, Any], einfo: Any) -> None:
        asyncio.run(ingest.mark_failed(UUID(str(args[0])), str(exc)))


class DeleteTask(Task):
    """Without this, an exhausted delete retry fails silently: the document
    sits forever in the "deleting" status set by the route with no error and
    no way for the UI to tell the user anything went wrong."""

    def on_failure(self, exc: Exception, task_id: str, args: tuple[Any, ...],
                   kwargs: dict[str, Any], einfo: Any) -> None:
        asyncio.run(ingest.mark_failed(UUID(str(args[0])), f"delete failed: {exc}"))


def _run(self: Task, coro_factory: Any) -> None:
    try:
        asyncio.run(coro_factory())
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
    _run(self, lambda: ingest.run_embed_upsert(UUID(document_id)))
    return document_id


@celery_app.task(base=DeleteTask, bind=True, max_retries=_MAX_RETRIES, name="documents.delete")
def delete_task(self: Task, document_id: str, actor_id: str | None = None) -> None:
    try:
        asyncio.run(ingest.run_delete(UUID(document_id),
                                      UUID(actor_id) if actor_id else None))
    except Exception as exc:
        raise self.retry(exc=exc, countdown=2 ** self.request.retries) from exc


@celery_app.task(base=IngestTask, bind=True, max_retries=_MAX_RETRIES, name="documents.reindex")
def reindex_task(self: Task, document_id: str) -> str:
    """Re-run embed+upsert from stored chunks.json (deterministic point ids
    make this idempotent) when promote_lineage picks a winner whose points
    were previously deleted (DOC-5). Its tail re-runs promote_lineage."""
    _run(self, lambda: ingest.run_embed_upsert(UUID(document_id)))
    return document_id


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
