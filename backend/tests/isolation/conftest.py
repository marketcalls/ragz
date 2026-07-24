import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from raghub.modules.auth.models import User
from raghub.modules.documents.ingest import run_chunk, run_embed_upsert, run_parse
from raghub.modules.documents.models import Document
from raghub.modules.documents.service import create_from_upload
from raghub.modules.retrieval.service import resolve_collection_name, update_document_current
from raghub.modules.tenancy.context import TenantContext
from raghub.modules.tenancy.models import Group, Organization, UserGroup, Workspace, WorkspaceMember
from tests.modules.retrieval.test_retrieve import seed_workspace


async def ingest_text(
    session: AsyncSession, ctx: TenantContext, ws: Workspace, filename: str, text: str
) -> Document:
    """Seed via the REAL pipeline: upload service -> parse -> chunk -> embed+upsert.

    Plan H: upsert_points always stamps is_current=False (invisible until
    promotion — Task 6). Real promotion doesn't exist yet, so this fixture
    flips visibility itself via the sanctioned update_document_current path —
    standing in for "this freshly-ingested version was promoted."
    """
    doc = await create_from_upload(session, ctx, ws.id, filename=filename,
                                   mime="text/plain", data=text.encode())
    await run_parse(doc.id)
    await run_chunk(doc.id)
    await run_embed_upsert(doc.id)
    collection_name = await resolve_collection_name(session, ws.id)
    await update_document_current(
        ctx.org_id, doc.id, is_current=True, collection_name=collection_name
    )
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


async def seed_acl_workspace(
    session: AsyncSession,
) -> tuple[TenantContext, TenantContext, TenantContext, Workspace, Group]:
    """ONE org, ONE workspace, both users members of it — so workspace filters
    alone can never explain a pass. insider is in group 'finance'; outsider is
    not; admin has no groups at all (bypass must come from role, not data)."""
    org = Organization(name="aclOrg")
    session.add(org)
    await session.flush()
    ws = Workspace(org_id=org.id, name="aclws")
    insider = User(org_id=org.id, email="in@aclorg.com", password_hash="x", role="user")  # noqa: S106
    outsider = User(org_id=org.id, email="out@aclorg.com", password_hash="x", role="user")  # noqa: S106
    admin = User(org_id=org.id, email="adm@aclorg.com", password_hash="x", role="admin")  # noqa: S106
    session.add_all([ws, insider, outsider, admin])
    await session.flush()
    finance = Group(org_id=org.id, name="finance")
    session.add(finance)
    session.add(WorkspaceMember(workspace_id=ws.id, user_id=insider.id))
    session.add(WorkspaceMember(workspace_id=ws.id, user_id=outsider.id))
    await session.flush()
    session.add(UserGroup(group_id=finance.id, user_id=insider.id))
    await session.commit()
    ctx_in = TenantContext(user_id=insider.id, org_id=org.id, role="user",
                           workspace_ids=frozenset({ws.id}),
                           group_ids=frozenset({finance.id}))
    ctx_out = TenantContext(user_id=outsider.id, org_id=org.id, role="user",
                            workspace_ids=frozenset({ws.id}))
    ctx_admin = TenantContext(user_id=admin.id, org_id=org.id, role="admin",
                              workspace_ids=frozenset())
    return ctx_in, ctx_out, ctx_admin, ws, finance


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
