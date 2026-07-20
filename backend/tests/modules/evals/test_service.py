"""Golden-query admin CRUD (Phase 3 §6). Org-scoped, workspace-owned."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from raghub.core.errors import NotFoundError, WorkspaceAccessDenied
from raghub.modules.documents.service import create_from_upload
from raghub.modules.evals import service
from raghub.modules.evals.models import EvalRun
from raghub.modules.tenancy.context import TenantContext
from raghub.modules.tenancy.models import Workspace


async def test_create_and_list_golden_query(
    session: AsyncSession, ctx: TenantContext, ws: Workspace
) -> None:
    doc = await create_from_upload(
        session, ctx, ws.id, filename="a.pdf", mime="application/pdf", data=b"x"
    )
    gq = await service.create_golden_query(
        session, ctx, ws.id, question="Where is the muster point?",
        expected_document_ids=[doc.id],
    )
    assert gq.question == "Where is the muster point?"
    listed = await service.list_golden_queries(session, ctx, ws.id)
    assert [g.id for g in listed] == [gq.id]


async def test_create_rejects_document_outside_workspace(
    session: AsyncSession, ctx: TenantContext, ws: Workspace, other_ws_document,
) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(NotFoundError):
        await service.create_golden_query(
            session, ctx, ws.id, question="q", expected_document_ids=[other_ws_document.id]
        )


async def test_create_allows_zero_expected_documents(
    session: AsyncSession, ctx: TenantContext, ws: Workspace
) -> None:
    gq = await service.create_golden_query(
        session, ctx, ws.id, question="Off-corpus question", expected_document_ids=[]
    )
    assert gq.expected_document_ids == []


async def test_delete_golden_query(
    session: AsyncSession, ctx: TenantContext, ws: Workspace
) -> None:
    gq = await service.create_golden_query(
        session, ctx, ws.id, question="q", expected_document_ids=[]
    )
    await service.delete_golden_query(session, ctx, gq.id)
    assert await service.list_golden_queries(session, ctx, ws.id) == []


async def test_delete_golden_query_missing_id_is_not_found(
    session: AsyncSession, ctx: TenantContext, ws: Workspace
) -> None:
    from uuid import uuid4

    with pytest.raises(NotFoundError):
        await service.delete_golden_query(session, ctx, uuid4())


async def test_delete_golden_query_rejects_sibling_workspace_non_member(
    session: AsyncSession, ctx: TenantContext, ws: Workspace
) -> None:
    """Review-fix regression test: delete_golden_query must reuse
    get_workspace_checked (as create_golden_query/list_golden_queries/
    list_eval_runs all do) rather than a bare Workspace.org_id join, so a
    same-org custom-role "user" who isn't a member of the query's OWNING
    workspace can't delete it just by guessing its UUID -- even though they
    ARE a member of some other workspace in the same org."""
    from raghub.modules.tenancy.models import WorkspaceMember

    sibling_ws = Workspace(org_id=ctx.org_id, name="sibling-ws")
    session.add(sibling_ws)
    await session.flush()
    await session.commit()
    gq = await service.create_golden_query(
        session, ctx, sibling_ws.id, question="sibling question", expected_document_ids=[]
    )

    from raghub.modules.auth.models import User

    member_user = User(
        org_id=ctx.org_id, email="member-only-ws@acme.com", password_hash="x", role="user",  # noqa: S106
    )
    session.add(member_user)
    await session.flush()
    session.add(WorkspaceMember(workspace_id=ws.id, user_id=member_user.id))
    await session.commit()
    member_ctx = TenantContext(
        user_id=member_user.id, org_id=ctx.org_id, role="user",
        workspace_ids=frozenset({ws.id}),
    )

    with pytest.raises(WorkspaceAccessDenied):
        await service.delete_golden_query(session, member_ctx, gq.id)
    # never post-filtered / silently dropped -- the query still exists
    assert await service.list_golden_queries(session, ctx, sibling_ws.id) == [gq]


async def test_workspace_ids_with_golden_queries_excludes_workspaces_without_any(
    session: AsyncSession, ctx: TenantContext, ws: Workspace
) -> None:
    """Backs the nightly fan-out (Task 12): only workspaces with >=1 golden
    query are worth a run."""
    other_ws = Workspace(org_id=ctx.org_id, name="no-golden-queries")
    session.add(other_ws)
    await session.flush()
    await session.commit()
    await service.create_golden_query(session, ctx, ws.id, question="q", expected_document_ids=[])
    ids = await service.workspace_ids_with_golden_queries(session)
    assert ws.id in ids
    assert other_ws.id not in ids


async def test_has_any_golden_query(
    session: AsyncSession, ctx: TenantContext, ws: Workspace
) -> None:
    assert await service.has_any_golden_query(session, ws.id) is False
    await service.create_golden_query(session, ctx, ws.id, question="q", expected_document_ids=[])
    assert await service.has_any_golden_query(session, ws.id) is True


async def test_latest_eval_run_per_workspace_returns_newest_first_with_workspace_name(
    session: AsyncSession, ctx: TenantContext, ws: Workspace
) -> None:
    from datetime import datetime

    older = EvalRun(
        workspace_id=ws.id, triggered_by="manual", hit_rate=0.1,
        created_at=datetime(2026, 1, 1),
    )
    newer = EvalRun(
        workspace_id=ws.id, triggered_by="nightly", hit_rate=0.9,
        created_at=datetime(2026, 1, 2),
    )
    session.add_all([older, newer])
    await session.commit()

    other_org_ws = Workspace(org_id=ctx.org_id, name="other-workspace")
    session.add(other_org_ws)
    await session.flush()
    session.add(EvalRun(workspace_id=other_org_ws.id, triggered_by="manual", hit_rate=0.4))
    await session.commit()

    trend = await service.latest_eval_run_per_workspace(session, ctx.org_id)
    by_ws = {t.workspace_id: t for t in trend}
    assert by_ws[ws.id].hit_rate == 0.9  # the newer of the two ws runs, not the older
    assert by_ws[ws.id].workspace_name == ws.name  # type: ignore[attr-defined]
    assert by_ws[other_org_ws.id].hit_rate == 0.4
