"""Plan J Task 12: the nightly eval fan-out. Mirrors the existing task tests
in test_celery.py for structure, but this is the first worker task that
touches a real DB from within its own asyncio.run()-wrapped closure, so the
seeding here goes through the `session`/`stack_env` fixtures (same
committed-data-is-visible-to-a-second-connection pattern modules/evals uses)
and the sync Celery task is invoked from a worker thread (asyncio.to_thread)
so its internal asyncio.run() never collides with the test's own running
event loop."""

import asyncio
from datetime import timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from raghub.core.db import naive_utc
from raghub.core.storage import ObjectStorage
from raghub.modules.auth.models import User
from raghub.modules.chat.models import Chat, ChatAttachment
from raghub.modules.evals.models import GoldenQuery
from raghub.modules.tenancy.models import Organization, Workspace
from raghub.worker import tasks


async def test_run_all_workspaces_enqueues_only_workspaces_with_golden_queries(
    session: AsyncSession, stack_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    org = Organization(name="WorkerEvalOrg")
    session.add(org)
    await session.flush()
    user = User(
        org_id=org.id, email="worker@evalorg.com", password_hash="x", role="admin"  # noqa: S106
    )
    ws_a = Workspace(org_id=org.id, name="ws-a")
    ws_b = Workspace(org_id=org.id, name="ws-b")  # no golden queries -- must be skipped
    session.add_all([user, ws_a, ws_b])
    await session.flush()
    session.add(GoldenQuery(workspace_id=ws_a.id, question="q", created_by=user.id))
    await session.commit()

    enqueued: list[tuple[object, str]] = []
    monkeypatch.setattr(
        "raghub.worker.tasks.enqueue_eval_run",
        lambda workspace_id, triggered_by: enqueued.append((workspace_id, triggered_by)),
    )

    await asyncio.to_thread(tasks.run_all_workspaces_task)

    assert enqueued == [(ws_a.id, "nightly")]


async def test_cleanup_stale_attachments_only_evicts_the_stale_attachments_vectors(
    session: AsyncSession, qdrant_collection: None, storage: ObjectStorage,
) -> None:
    """Whole-branch review fix (DOC-9 final review): a chat holding BOTH a
    >24h attachment (swept by this task) and a fresh (<24h) retrieval-routed
    attachment must only lose the STALE one's Qdrant points -- the fresh
    sibling's points must survive untouched, since it hasn't hit its OWN 24h
    TTL yet. Before the fix, cleanup_stale_attachments_task called
    delete_ephemeral_points(chat_id) once per affected chat_id, wiping the
    WHOLE chat's ephemeral points regardless of which attachment(s) were
    actually stale.

    Mirrors test_attachment_cleanup.py's real Chat/ChatAttachment fixture
    setup, test_ephemeral_collection.py's real-points upsert/search
    conventions, and this file's own asyncio.to_thread pattern for invoking
    a sync Celery task from an async test."""
    from raghub.modules.documents.pipeline import Chunk
    from raghub.modules.models import service as models_service
    from raghub.modules.models.models import LOCAL_EMBEDDING_MODEL_ID
    from raghub.modules.retrieval.embeddings import embed_sparse, get_dense_embedder
    from raghub.modules.retrieval.service import (
        ensure_ephemeral_collection,
        search_ephemeral_attachments,
        upsert_ephemeral_chunks,
    )

    org = Organization(name="TTLCleanupOrg")
    session.add(org)
    await session.flush()
    user = User(
        org_id=org.id, email="ttl@cleanuporg.com", password_hash="x", role="admin"  # noqa: S106
    )
    ws = Workspace(org_id=org.id, name="ws")
    session.add_all([user, ws])
    await session.flush()
    chat = Chat(org_id=org.id, workspace_id=ws.id, user_id=user.id)
    session.add(chat)
    await session.flush()

    stale_attachment = ChatAttachment(
        chat_id=chat.id, kind="document", filename="stale.txt", mime="text/plain",
        storage_key=f"{org.id}/chats/{chat.id}/stale.txt", status="ready",
        routed_to="retrieval",
    )
    fresh_attachment = ChatAttachment(
        chat_id=chat.id, kind="document", filename="fresh.txt", mime="text/plain",
        storage_key=f"{org.id}/chats/{chat.id}/fresh.txt", status="ready",
        routed_to="retrieval",
    )
    session.add_all([stale_attachment, fresh_attachment])
    await session.flush()
    # Captured now (plain UUIDs, not ORM attribute access) -- used after
    # session.expire_all() below, where touching an expired instance's
    # attribute outside an awaited ORM call raises MissingGreenlet.
    stale_attachment_id, fresh_attachment_id = stale_attachment.id, fresh_attachment.id
    stale_attachment.created_at = naive_utc() - timedelta(hours=25)
    await storage.put(stale_attachment.storage_key, b"stale content", content_type="text/plain")
    await storage.put(fresh_attachment.storage_key, b"fresh content", content_type="text/plain")
    await session.commit()

    await ensure_ephemeral_collection()
    # DOC-10: the ephemeral store has no per-workspace embedding choice --
    # always the seeded local model (mirrors chat/service.py's route_attachment).
    ephemeral_model = await models_service.get_model(session, LOCAL_EMBEDDING_MODEL_ID)
    embedder = get_dense_embedder(
        ephemeral_model.id, provider_kind=ephemeral_model.provider_kind,
        litellm_model_name=ephemeral_model.litellm_model_name,
    )
    for attachment, text in [
        (stale_attachment, "the quarterly report shows a decline"),
        (fresh_attachment, "the quarterly report shows growth"),
    ]:
        chunk = Chunk(text=text, page=1, chunk_index=0)
        dense = (await embedder.embed([text]))[0]
        sparse = embed_sparse([text])[0]
        await upsert_ephemeral_chunks(
            org_id=org.id, chat_id=chat.id, attachment_id=attachment.id,
            chunks=[chunk], dense=[dense], sparse=[sparse],
        )

    await asyncio.to_thread(tasks.cleanup_stale_attachments_task)

    query_dense = (await embedder.embed(["quarterly report"]))[0]
    query_sparse = embed_sparse(["quarterly report"])[0]
    hits = await search_ephemeral_attachments(
        org_id=org.id, chat_id=chat.id, query_dense=query_dense,
        query_sparse=query_sparse, top_k=5,
    )
    texts = {h.text for h in hits}
    assert "the quarterly report shows growth" in texts, (
        "fresh attachment's vectors must survive a sweep that only the "
        "sibling stale attachment triggered"
    )
    assert "the quarterly report shows a decline" not in texts

    # DB-row side: the stale attachment's row is gone, the fresh one's is not.
    # This session's factory uses expire_on_commit=False (core/db.py), so the
    # two objects loaded above are still cached in the identity map -- force
    # a re-fetch to see the OTHER session's (the task's) committed delete.
    session.expire_all()
    assert await session.get(ChatAttachment, stale_attachment_id) is None
    assert await session.get(ChatAttachment, fresh_attachment_id) is not None
