import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from raghub.modules.auth.models import User
from raghub.modules.documents.ingest import run_chunk, run_embed_upsert, run_parse
from raghub.modules.documents.models import Document
from raghub.modules.documents.service import create_from_upload
from raghub.modules.tenancy.context import TenantContext
from raghub.modules.tenancy.models import Organization, Workspace, WorkspaceMember
from tests.modules.retrieval.test_retrieve import seed_workspace


async def ingest_text(
    session: AsyncSession, ctx: TenantContext, ws: Workspace, filename: str, text: str
) -> Document:
    """Seed via the REAL pipeline: upload service -> parse -> chunk -> embed+upsert."""
    doc = await create_from_upload(session, ctx, ws.id, filename=filename,
                                   mime="text/plain", data=text.encode())
    await run_parse(doc.id)
    await run_chunk(doc.id)
    await run_embed_upsert(doc.id)
    await session.refresh(doc)
    assert doc.status == "indexed"
    return doc


async def seed_same_org_two_workspaces(
    session: AsyncSession,
) -> tuple[TenantContext, Workspace, TenantContext, Workspace]:
    """ONE org, TWO workspaces — the real product-leak scenario: same tenant_id,
    different workspace_id. Each workspace gets its own member-only user so a
    ctx scoped to ws1 can never legitimately reach ws2's documents."""
    org = Organization(name="isoSameOrg")
    session.add(org)
    await session.flush()
    ws1 = Workspace(org_id=org.id, name="ws1")
    ws2 = Workspace(org_id=org.id, name="ws2")
    user1 = User(org_id=org.id, email="u1@isosameorg.com", password_hash="x", role="user")  # noqa: S106
    user2 = User(org_id=org.id, email="u2@isosameorg.com", password_hash="x", role="user")  # noqa: S106
    session.add_all([ws1, ws2, user1, user2])
    await session.flush()
    session.add(WorkspaceMember(workspace_id=ws1.id, user_id=user1.id))
    session.add(WorkspaceMember(workspace_id=ws2.id, user_id=user2.id))
    await session.commit()
    ctx1 = TenantContext(
        user_id=user1.id, org_id=org.id, role="user", workspace_ids=frozenset({ws1.id})
    )
    ctx2 = TenantContext(
        user_id=user2.id, org_id=org.id, role="user", workspace_ids=frozenset({ws2.id})
    )
    return ctx1, ws1, ctx2, ws2


@pytest.fixture
async def two_orgs(
    session: AsyncSession, qdrant_collection: None
) -> dict[str, tuple[TenantContext, Workspace, Document]]:
    ctx_a, ws_a = await seed_workspace(session, "isoA")
    ctx_b, ws_b = await seed_workspace(session, "isoB")
    doc_a = await ingest_text(session, ctx_a, ws_a, "a.txt",
                              "org alpha secret: the vault code is 7431")
    doc_b = await ingest_text(session, ctx_b, ws_b, "b.txt",
                              "org bravo secret: the vault code is 9962")
    return {"a": (ctx_a, ws_a, doc_a), "b": (ctx_b, ws_b, doc_b)}
