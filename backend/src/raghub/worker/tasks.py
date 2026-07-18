"""Thin sync wrappers over async runners (ADR-0001: asyncio.run per task).

No business logic lives here — only retry/queue/failure plumbing.
"""

import asyncio
from typing import Any
from uuid import UUID

from celery import Task, chain

from raghub.core.config import get_settings
from raghub.modules.documents import ingest
from raghub.modules.documents.pipeline import IngestFailure
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
