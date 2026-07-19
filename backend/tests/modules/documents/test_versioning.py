"""Effective-current promotion semantics (DOC-5 + approved preference).

upload v1 -> index -> v1 current
upload v2 -> index -> v2 current, v1 demoted, v1 points deleted
approve v1 only    -> v1 current again (approved beats newer-unapproved) via reindex
approve v2 as well -> v2 current (highest approved wins)
"""

from sqlalchemy.ext.asyncio import AsyncSession

from raghub.modules.documents.ingest import run_chunk, run_delete, run_embed_upsert, run_parse
from raghub.modules.documents.models import Document
from raghub.modules.documents.service import create_from_upload, set_approved
from raghub.modules.tenancy.context import TenantContext
from raghub.modules.tenancy.models import Workspace
from tests.modules.retrieval.test_retrieve import seed_workspace


async def _index(
    session: AsyncSession, ctx: TenantContext, ws: Workspace, filename: str, text: str
) -> Document:
    """Real pipeline: upload -> parse -> chunk -> embed+upsert (which now ends
    by calling promote_lineage per DOC-5). No manual update_document_current
    stand-in needed anymore -- that was Task 5's placeholder."""
    doc = await create_from_upload(
        session, ctx, ws.id, filename=filename, mime="text/plain", data=text.encode()
    )
    await run_parse(doc.id)
    await run_chunk(doc.id)
    await run_embed_upsert(doc.id)
    await session.refresh(doc)
    return doc


async def test_first_index_promotes_v1(
    session: AsyncSession, qdrant_collection: None
) -> None:
    ctx, ws = await seed_workspace(session, "ver1")
    v1 = await _index(session, ctx, ws, "policy.txt", "the muster point is DOCK 4")
    assert v1.is_current is True
    assert v1.vectors_present is True


async def test_new_indexed_version_supersedes(
    session: AsyncSession, qdrant_collection: None
) -> None:
    ctx, ws = await seed_workspace(session, "ver2")
    v1 = await _index(session, ctx, ws, "policy.txt", "the muster point is DOCK 4")
    v2 = await _index(session, ctx, ws, "policy.txt", "the muster point is GATE 9")
    await session.refresh(v1)
    await session.refresh(v2)
    assert v2.is_current is True
    assert v1.is_current is False
    assert v1.vectors_present is False
    assert v2.vectors_present is True


async def test_approved_older_version_wins(
    session: AsyncSession, qdrant_collection: None
) -> None:
    ctx, ws = await seed_workspace(session, "ver3")
    v1 = await _index(session, ctx, ws, "policy.txt", "the muster point is DOCK 4")
    v1 = await set_approved(session, ctx, v1.id, True)
    v2 = await _index(session, ctx, ws, "policy.txt", "the muster point is GATE 9")
    await session.refresh(v1)
    await session.refresh(v2)
    assert v1.is_current is True
    assert v1.vectors_present is True
    assert v2.is_current is False
    assert v2.vectors_present is False  # points invisible: deleted on the same promotion


async def test_approving_pointless_version_enqueues_reindex(
    session: AsyncSession, qdrant_collection: None, monkeypatch  # type: ignore[no-untyped-def]
) -> None:
    ctx, ws = await seed_workspace(session, "ver4")
    v1 = await _index(session, ctx, ws, "policy.txt", "the muster point is DOCK 4")
    v2 = await _index(session, ctx, ws, "policy.txt", "the muster point is GATE 9")
    await session.refresh(v1)
    assert v1.vectors_present is False  # demoted+deleted when v2 was promoted

    calls: list = []  # type: ignore[type-arg]
    monkeypatch.setattr(
        "raghub.worker.tasks.enqueue_reindex", lambda document_id: calls.append(document_id)
    )

    v1 = await set_approved(session, ctx, v1.id, True)
    assert calls == [v1.id]  # no points to promote onto -> reindex enqueued instead
    await session.refresh(v1)
    await session.refresh(v2)
    assert v1.is_current is False  # unchanged: flags don't flip until reindex completes
    assert v2.is_current is True

    # Simulate the reindex task completing: run_embed_upsert re-reads chunks.json
    # (deterministic point ids) and its tail re-runs promote_lineage.
    await run_embed_upsert(v1.id)
    await session.refresh(v1)
    await session.refresh(v2)
    assert v1.is_current is True
    assert v2.is_current is False


async def test_delete_current_version_promotes_survivor(
    session: AsyncSession, qdrant_collection: None, monkeypatch  # type: ignore[no-untyped-def]
) -> None:
    ctx, ws = await seed_workspace(session, "ver5")
    v1 = await _index(session, ctx, ws, "policy.txt", "the muster point is DOCK 4")
    v2 = await _index(session, ctx, ws, "policy.txt", "the muster point is GATE 9")
    await session.refresh(v1)
    assert v1.vectors_present is False  # demoted+deleted when v2 was promoted

    calls: list = []  # type: ignore[type-arg]
    monkeypatch.setattr(
        "raghub.worker.tasks.enqueue_reindex", lambda document_id: calls.append(document_id)
    )

    await run_delete(v2.id, ctx.user_id)
    assert calls == [v1.id]  # only surviving version has no points -> reindex enqueued
    await session.refresh(v1)
    assert v1.is_current is False  # unchanged until the reindex completes

    await run_embed_upsert(v1.id)  # simulate the reindex task
    await session.refresh(v1)
    assert v1.is_current is True
    assert v1.vectors_present is True
