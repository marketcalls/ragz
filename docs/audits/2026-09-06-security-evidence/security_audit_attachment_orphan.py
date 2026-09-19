"""Retained regression proof for the chat-delete attachment orphan finding.

This test deliberately models MinIO/Qdrant as external key sets.  It proves the
database cascade removes the only rows the scheduled TTL sweep can enumerate,
while neither external resource is touched by ``delete_chat``.
"""

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ragz.core.db import naive_utc
from ragz.modules.auth.models import User
from ragz.modules.chat.models import ChatAttachment
from ragz.modules.chat.service import create_chat, delete_chat, list_stale_attachments
from ragz.modules.tenancy.context import TenantContext
from ragz.modules.tenancy.models import Workspace, WorkspaceMember


async def test_delete_chat_makes_attachment_blobs_and_vectors_undiscoverable(
    session: AsyncSession, seeded_user: User
) -> None:
    workspace = Workspace(org_id=seeded_user.org_id, name="orphan-proof")
    session.add(workspace)
    await session.flush()
    session.add(WorkspaceMember(workspace_id=workspace.id, user_id=seeded_user.id))
    await session.commit()
    ctx = TenantContext(
        user_id=seeded_user.id,
        org_id=seeded_user.org_id,
        role=seeded_user.role,
        workspace_ids=frozenset({workspace.id}),
    )
    chat = await create_chat(session, ctx, workspace_id=workspace.id)
    attachment = ChatAttachment(
        chat_id=chat.id,
        kind="document",
        filename="large.txt",
        mime="text/plain",
        storage_key=f"{ctx.org_id}/chats/{chat.id}/attachment/large.txt",
        status="ready",
        routed_to="retrieval",
    )
    session.add(attachment)
    await session.commit()
    attachment_id = attachment.id
    storage_key = attachment.storage_key

    # Deterministic stand-ins for independently durable MinIO/Qdrant state.
    minio_keys = {storage_key}
    qdrant_attachment_ids = {attachment_id}

    await delete_chat(session, ctx, chat.id)

    # Bypass the identity map: expire_on_commit=False intentionally retains the
    # pre-cascade object instance even though its row is gone in PostgreSQL.
    persisted_attachment = (
        await session.execute(
            select(ChatAttachment)
            .where(ChatAttachment.id == attachment_id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    assert persisted_attachment is None
    assert storage_key in minio_keys
    assert attachment_id in qdrant_attachment_ids

    # Even an all-inclusive future cutoff cannot discover the cascaded row.
    # cleanup_stale_attachments_task iterates exactly this query's result, so it
    # has no key/id with which to call storage.delete/delete_ephemeral_points.
    stale = await list_stale_attachments(session, naive_utc() + timedelta(days=3650))
    assert attachment_id not in {row.id for row in stale}
