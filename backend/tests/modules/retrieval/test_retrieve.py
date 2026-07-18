import asyncio
from uuid import uuid4

import pytest
from qdrant_client import models
from sqlalchemy.ext.asyncio import AsyncSession

from raghub.core.errors import WorkspaceAccessDenied
from raghub.modules.auth.models import User
from raghub.modules.retrieval.client import COLLECTION, get_qdrant
from raghub.modules.retrieval.embeddings import embed_sparse, get_dense_embedder
from raghub.modules.retrieval.service import delete_document_points, retrieve
from raghub.modules.tenancy.context import TenantContext
from raghub.modules.tenancy.models import Organization, Workspace, WorkspaceMember


async def seed_workspace(
    session: AsyncSession, org_name: str, *, role: str = "user", member: bool = True,
    min_score: float = 0.0,
) -> tuple[TenantContext, Workspace]:
    org = Organization(name=org_name)
    session.add(org)
    await session.flush()
    ws = Workspace(org_id=org.id, name="ws", min_score=min_score)
    user = User(org_id=org.id, email=f"u@{org_name}.com", password_hash="x", role=role)  # noqa: S106
    session.add_all([ws, user])
    await session.flush()
    if member:
        session.add(WorkspaceMember(workspace_id=ws.id, user_id=user.id))
    await session.commit()
    ctx = TenantContext(
        user_id=user.id, org_id=org.id, role=role,
        workspace_ids=frozenset({ws.id}) if member else frozenset(),
    )
    return ctx, ws


async def upsert_texts(ctx: TenantContext, ws: Workspace, texts: list[str]) -> str:
    """Test seeding via raw points; production code goes through the pipeline."""
    document_id = str(uuid4())
    dense = await get_dense_embedder().embed(texts)
    sparse = await asyncio.to_thread(embed_sparse, texts)
    points = [
        models.PointStruct(
            id=str(uuid4()),
            vector={"dense": d, "sparse": s},
            payload={"tenant_id": str(ctx.org_id), "workspace_id": str(ws.id),
                     "document_id": document_id, "page": i + 1, "chunk_index": i,
                     "text": t, "doc_type": "text/plain", "date": "2026-07-18",
                     "acl_groups": []},
        )
        for i, (t, d, s) in enumerate(zip(texts, dense, sparse, strict=True))
    ]
    await get_qdrant().upsert(COLLECTION, points=points, wait=True)
    return document_id


async def test_retrieve_returns_matching_chunk(
    session: AsyncSession, qdrant_collection: None
) -> None:
    ctx, ws = await seed_workspace(session, "orga")
    await upsert_texts(ctx, ws, ["the flux capacitor requires 1.21 gigawatts",
                                 "unrelated kumquat farming notes"])
    result = await retrieve(session, ctx, ws.id, "flux capacitor gigawatts", top_k=2)
    assert not result.no_answer
    assert result.chunks[0].text.startswith("the flux capacitor")
    assert result.chunks[0].page == 1 and result.chunks[0].chunk_index == 0


async def test_min_score_triggers_no_answer_with_nearest(
    session: AsyncSession, qdrant_collection: None
) -> None:
    ctx, ws = await seed_workspace(session, "orgb", min_score=0.99)
    await upsert_texts(ctx, ws, ["some vaguely related text about invoices"])
    result = await retrieve(session, ctx, ws.id, "completely different query terms")
    assert result.no_answer
    assert result.chunks  # nearest sources still surfaced (CHAT-9)


async def test_empty_workspace_is_no_answer(
    session: AsyncSession, qdrant_collection: None
) -> None:
    ctx, ws = await seed_workspace(session, "orgc")
    result = await retrieve(session, ctx, ws.id, "anything")
    assert result.no_answer and result.chunks == []


async def test_non_member_denied(session: AsyncSession, qdrant_collection: None) -> None:
    ctx, ws = await seed_workspace(session, "orgd", member=False)
    with pytest.raises(WorkspaceAccessDenied):
        await retrieve(session, ctx, ws.id, "anything")


async def test_admin_without_membership_allowed(
    session: AsyncSession, qdrant_collection: None
) -> None:
    ctx, ws = await seed_workspace(session, "orge", role="admin", member=False)
    result = await retrieve(session, ctx, ws.id, "anything")
    assert result.chunks == []


async def test_delete_document_points(session: AsyncSession, qdrant_collection: None) -> None:
    ctx, ws = await seed_workspace(session, "orgf")
    doc_id = await upsert_texts(ctx, ws, ["target text to delete"])
    from uuid import UUID
    await delete_document_points(ctx.org_id, UUID(doc_id))
    result = await retrieve(session, ctx, ws.id, "target text to delete")
    assert result.chunks == []
