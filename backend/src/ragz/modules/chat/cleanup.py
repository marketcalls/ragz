"""Durable, idempotent attachment cleanup and legacy orphan discovery."""

from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ragz.core.db import naive_utc
from ragz.modules.chat.models import AttachmentCleanupJob, ChatAttachment

DeleteBlob = Callable[[str], Awaitable[None]]
DeleteVectors = Callable[[UUID, UUID], Awaitable[None]]


async def schedule_cleanup(
    session: AsyncSession,
    attachment: ChatAttachment,
    *,
    org_id: UUID,
    user_id: UUID,
) -> AttachmentCleanupJob:
    existing = (
        await session.execute(
            select(AttachmentCleanupJob).where(
                AttachmentCleanupJob.attachment_id == attachment.id
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    job = AttachmentCleanupJob(
        org_id=org_id,
        user_id=user_id,
        chat_id=attachment.chat_id,
        attachment_id=attachment.id,
        storage_key=attachment.storage_key,
        size_bytes=attachment.size_bytes,
    )
    session.add(job)
    return job


async def list_due_cleanup_jobs(
    session: AsyncSession, *, limit: int = 200
) -> list[AttachmentCleanupJob]:
    return list(
        (
            await session.execute(
                select(AttachmentCleanupJob)
                .where(
                    AttachmentCleanupJob.completed_at.is_(None),
                    AttachmentCleanupJob.next_attempt_at <= naive_utc(),
                )
                .order_by(AttachmentCleanupJob.created_at)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        ).scalars()
    )


async def process_cleanup_job(
    session: AsyncSession,
    job: AttachmentCleanupJob,
    *,
    delete_blob: DeleteBlob,
    delete_vectors: DeleteVectors,
) -> bool:
    """Attempt both idempotent deletes and durably record retry/completion."""

    if job.completed_at is not None:
        return True
    try:
        await delete_blob(job.storage_key)
        await delete_vectors(job.chat_id, job.attachment_id)
    except Exception as exc:
        job.attempts += 1
        job.last_error = type(exc).__name__
        job.next_attempt_at = naive_utc() + timedelta(
            seconds=min(3600, 2 ** min(job.attempts, 10))
        )
        await session.commit()
        return False
    job.completed_at = naive_utc()
    job.last_error = None
    await session.commit()
    return True


@dataclass(frozen=True, slots=True)
class LegacyOrphanCandidates:
    object_attachment_ids: frozenset[UUID]
    vector_attachment_ids: frozenset[UUID]


def legacy_orphan_candidates(
    *,
    database_attachment_ids: Iterable[UUID],
    cleanup_job_attachment_ids: Iterable[UUID],
    object_attachment_ids: Iterable[UUID],
    vector_attachment_ids: Iterable[UUID],
) -> LegacyOrphanCandidates:
    """Return dry-run candidates only; callers must review before scheduling."""

    accounted = set(database_attachment_ids) | set(cleanup_job_attachment_ids)
    return LegacyOrphanCandidates(
        object_attachment_ids=frozenset(set(object_attachment_ids) - accounted),
        vector_attachment_ids=frozenset(set(vector_attachment_ids) - accounted),
    )
