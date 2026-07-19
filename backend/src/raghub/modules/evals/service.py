"""Golden-query admin CRUD (Phase 3 §6). Mirrors
modules/documents/metadata.py's shape: org-scoped via a workspace join,
unknown-reference -> NotFoundError (never silently ignored)."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from raghub.core.errors import NotFoundError
from raghub.modules.audit.service import record_audit
from raghub.modules.documents.models import Document
from raghub.modules.evals.models import GoldenQuery
from raghub.modules.tenancy.context import TenantContext
from raghub.modules.tenancy.models import Workspace
from raghub.modules.tenancy.service import get_workspace_checked


async def create_golden_query(
    session: AsyncSession, ctx: TenantContext, workspace_id: UUID, *,
    question: str, expected_document_ids: list[UUID],
) -> GoldenQuery:
    ws = await get_workspace_checked(session, ctx, workspace_id)
    if expected_document_ids:
        found = set(
            (
                await session.execute(
                    select(Document.id).where(
                        Document.workspace_id == ws.id,
                        Document.id.in_(expected_document_ids),
                    )
                )
            ).scalars()
        )
        missing = set(expected_document_ids) - found
        if missing:
            raise NotFoundError(f"document(s) not in this workspace: {sorted(missing)}")
    gq = GoldenQuery(
        workspace_id=ws.id, question=question,
        expected_document_ids=list(expected_document_ids), created_by=ctx.user_id,
    )
    session.add(gq)
    await session.flush()
    await record_audit(
        session, org_id=ctx.org_id, actor_id=ctx.user_id, action="golden_query.created",
        target_type="golden_query", target_id=str(gq.id),
    )
    await session.commit()
    return gq


async def list_golden_queries(
    session: AsyncSession, ctx: TenantContext, workspace_id: UUID
) -> list[GoldenQuery]:
    await get_workspace_checked(session, ctx, workspace_id)
    return list(
        (
            await session.execute(
                select(GoldenQuery)
                .where(GoldenQuery.workspace_id == workspace_id)
                .order_by(GoldenQuery.created_at)
            )
        ).scalars()
    )


async def delete_golden_query(session: AsyncSession, ctx: TenantContext, query_id: UUID) -> None:
    gq = (
        await session.execute(
            select(GoldenQuery)
            .join(Workspace, Workspace.id == GoldenQuery.workspace_id)
            .where(GoldenQuery.id == query_id, Workspace.org_id == ctx.org_id)
        )
    ).scalar_one_or_none()
    if gq is None:
        raise NotFoundError("golden query not found")
    await session.delete(gq)
    await record_audit(
        session, org_id=ctx.org_id, actor_id=ctx.user_id, action="golden_query.deleted",
        target_type="golden_query", target_id=str(query_id),
    )
    await session.commit()
