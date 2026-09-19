import asyncio
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ragz.core.config import Settings
from ragz.core.errors import OrgResourceQuotaExceeded
from ragz.modules.auth.models import User
from ragz.modules.chat import attachments as attachment_service
from ragz.modules.chat.chats import delete_chat
from ragz.modules.chat.cleanup import legacy_orphan_candidates, process_cleanup_job
from ragz.modules.chat.models import AttachmentCleanupJob, Chat, ChatAttachment
from ragz.modules.tenancy.context import TenantContext
from ragz.modules.tenancy.models import Organization, Workspace


async def _attachment_fixture(
    session: AsyncSession,
) -> tuple[TenantContext, Chat, ChatAttachment]:
    org = Organization(name="Cleanup lifecycle")
    session.add(org)
    await session.flush()
    user = User(
        org_id=org.id,
        email="cleanup-lifecycle@example.com",
        password_hash="x",  # noqa: S106
        role="admin",
    )
    workspace = Workspace(org_id=org.id, name="Cleanup")
    session.add_all([user, workspace])
    await session.flush()
    chat = Chat(org_id=org.id, workspace_id=workspace.id, user_id=user.id)
    session.add(chat)
    await session.flush()
    attachment = ChatAttachment(
        chat_id=chat.id,
        kind="document",
        filename="evidence.txt",
        mime="text/plain",
        storage_key=f"{org.id}/chats/{chat.id}/evidence.txt",
        size_bytes=8,
        status="ready",
        routed_to="retrieval",
    )
    session.add(attachment)
    await session.commit()
    return (
        TenantContext(
            user_id=user.id,
            org_id=org.id,
            role="admin",
            workspace_ids=frozenset(),
        ),
        chat,
        attachment,
    )


async def test_chat_delete_persists_external_identifiers_before_attachment_cascade(
    session: AsyncSession,
) -> None:
    ctx, chat, attachment = await _attachment_fixture(session)
    attachment_id = attachment.id
    storage_key = attachment.storage_key
    chat_id = chat.id

    await delete_chat(session, ctx, chat_id)

    session.expire_all()
    assert await session.get(ChatAttachment, attachment_id) is None
    job = (
        await session.execute(
            select(AttachmentCleanupJob).where(
                AttachmentCleanupJob.attachment_id == attachment_id
            )
        )
    ).scalar_one()
    assert job.chat_id == chat_id
    assert job.storage_key == storage_key
    assert job.completed_at is None
    assert job.attempts == 0


async def test_cleanup_retries_store_failure_and_completes_idempotently(
    session: AsyncSession,
) -> None:
    ctx, chat, attachment = await _attachment_fixture(session)
    await delete_chat(session, ctx, chat.id)
    job = (
        await session.execute(
            select(AttachmentCleanupJob).where(
                AttachmentCleanupJob.attachment_id == attachment.id
            )
        )
    ).scalar_one()
    calls: list[tuple[str, str]] = []

    async def fail_blob(storage_key: str) -> None:
        calls.append(("blob", storage_key))
        raise RuntimeError("synthetic isolated store failure")

    async def delete_vectors(chat_id, attachment_id) -> None:  # type: ignore[no-untyped-def]
        calls.append(("vectors", str(attachment_id)))

    assert not await process_cleanup_job(
        session, job, delete_blob=fail_blob, delete_vectors=delete_vectors
    )
    await session.refresh(job)
    assert job.attempts == 1
    assert job.last_error == "RuntimeError"
    assert job.completed_at is None
    assert [kind for kind, _ in calls] == ["blob"]

    async def delete_blob(storage_key: str) -> None:
        calls.append(("blob", storage_key))

    assert await process_cleanup_job(
        session, job, delete_blob=delete_blob, delete_vectors=delete_vectors
    )
    await session.refresh(job)
    assert job.completed_at is not None
    assert [kind for kind, _ in calls] == ["blob", "blob", "vectors"]

    assert await process_cleanup_job(
        session, job, delete_blob=delete_blob, delete_vectors=delete_vectors
    )
    assert [kind for kind, _ in calls] == ["blob", "blob", "vectors"]


async def test_pending_external_cleanup_keeps_storage_quota_until_completion(
    session: AsyncSession,
) -> None:
    """Removing this cleanup-job charge would reopen upload/delete quota cycling."""

    ctx, chat, attachment = await _attachment_fixture(session)
    settings = Settings(
        _env_file=None,
        environment="test",
        attachment_max_bytes_per_org=attachment.size_bytes,
        attachment_max_bytes_per_user=attachment.size_bytes,
    )
    await delete_chat(session, ctx, chat.id)
    job = (
        await session.execute(
            select(AttachmentCleanupJob).where(
                AttachmentCleanupJob.attachment_id == attachment.id
            )
        )
    ).scalar_one()
    job_id = job.id

    with pytest.raises(OrgResourceQuotaExceeded, match="storage limit"):
        await attachment_service._reserve_attachment(
            session, ctx, size_bytes=1, settings=settings
        )
    await session.rollback()
    job = await session.get(AttachmentCleanupJob, job_id)
    assert job is not None

    async def delete_blob(_storage_key: str) -> None:
        return None

    async def delete_vectors(_chat_id, _attachment_id) -> None:  # type: ignore[no-untyped-def]
        return None

    assert await process_cleanup_job(
        session, job, delete_blob=delete_blob, delete_vectors=delete_vectors
    )
    reservation_id = await attachment_service._reserve_attachment(
        session, ctx, size_bytes=1, settings=settings
    )
    await attachment_service.resource_admission.release(session, reservation_id)


async def test_committed_attachment_survives_cancelled_commit_acknowledgement(
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A commit acknowledgement failure must not compensate a durable row's blob."""

    ctx, chat, _existing = await _attachment_fixture(session)
    blobs: set[str] = set()

    class Storage:
        async def ensure_bucket(self) -> None:
            return None

        async def put_stream(self, key, stream, content_type):  # type: ignore[no-untyped-def]
            blobs.add(key)

        async def delete(self, key: str) -> None:
            blobs.discard(key)

    monkeypatch.setattr(attachment_service, "build_storage", lambda _settings: Storage())
    real_commit = session.commit
    commits = 0

    async def committed_then_cancelled() -> None:
        nonlocal commits
        commits += 1
        await real_commit()
        if commits == 2:
            raise asyncio.CancelledError("synthetic cancellation after durable commit")

    monkeypatch.setattr(session, "commit", committed_then_cancelled)
    with pytest.raises(asyncio.CancelledError):
        await attachment_service.create_attachment(
            session,
            ctx,
            chat.id,
            filename="committed.txt",
            mime="text/plain",
            data=b"durable",
            settings=Settings(_env_file=None, environment="test"),
        )
    row = (
        await session.execute(
            select(ChatAttachment).where(ChatAttachment.filename == "committed.txt")
        )
    ).scalar_one()
    assert row.storage_key in blobs


def test_legacy_orphan_discovery_is_read_only_set_reconciliation() -> None:
    referenced = {uuid4()}
    already_queued = {uuid4()}
    orphaned = {uuid4(), uuid4()}

    result = legacy_orphan_candidates(
        database_attachment_ids=referenced,
        cleanup_job_attachment_ids=already_queued,
        object_attachment_ids=referenced | already_queued | orphaned,
        vector_attachment_ids=referenced | orphaned,
    )

    assert result.object_attachment_ids == orphaned
    assert result.vector_attachment_ids == orphaned
