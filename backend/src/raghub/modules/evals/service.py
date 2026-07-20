"""Golden-query admin CRUD (Phase 3 §6). Mirrors
modules/documents/metadata.py's shape: org-scoped via a workspace join,
unknown-reference -> NotFoundError (never silently ignored)."""

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from raghub.core.errors import NotFoundError
from raghub.modules.audit.service import record_audit
from raghub.modules.documents.models import Document
from raghub.modules.evals.models import EvalRun, GoldenQuery
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


async def list_golden_queries_for_run(
    session: AsyncSession, workspace_id: UUID
) -> list[GoldenQuery]:
    """Ctx-free sibling of list_golden_queries, for worker/route-triggered
    runner invocations (Task 11). Unguarded: the CALLER (the route that already
    ran ConfigureDep, or the Celery task it enqueued) is the trust boundary —
    mirrors Task 3's audit_message's ctx-free posture."""
    return list(
        (
            await session.execute(
                select(GoldenQuery)
                .where(GoldenQuery.workspace_id == workspace_id)
                .order_by(GoldenQuery.created_at)
            )
        ).scalars()
    )


async def check_workspace_for_trigger(
    session: AsyncSession, ctx: TenantContext, workspace_id: UUID
) -> None:
    """Workspace-scoping gate for the on-demand eval-run trigger route (Task 11
    review fix). Reuses the same check as list_golden_queries/list_eval_runs
    below (get_workspace_checked -- raises WorkspaceAccessDenied for a
    workspace_id that isn't in ctx.org_id, or that the caller isn't a member
    of), so a user with workspace.configure in one org cannot enqueue a run --
    and burn LLM/quota budget -- against another org's workspace by guessing
    its UUID."""
    await get_workspace_checked(session, ctx, workspace_id)


async def list_eval_runs(
    session: AsyncSession, ctx: TenantContext, workspace_id: UUID, limit: int = 50
) -> list[EvalRun]:
    """Org-scoped via the workspace join (mirrors list_golden_queries),
    newest-first, capped at `limit`."""
    await get_workspace_checked(session, ctx, workspace_id)
    return list(
        (
            await session.execute(
                select(EvalRun)
                .where(EvalRun.workspace_id == workspace_id)
                .order_by(EvalRun.created_at.desc())
                .limit(limit)
            )
        ).scalars()
    )


async def workspace_ids_with_golden_queries(session: AsyncSession) -> list[UUID]:
    """Ctx-free, worker-side (Task 12's nightly beat fan-out) -- distinct
    workspace ids with >=1 golden query, across every org. Unguarded like
    list_golden_queries_for_run: the CALLER (the beat-scheduled task) is the
    trust boundary, not this function."""
    return list(
        (await session.execute(select(GoldenQuery.workspace_id).distinct())).scalars()
    )


async def has_any_golden_query(session: AsyncSession, workspace_id: UUID) -> bool:
    """Cheap existence check backing the settings-change eval trigger (Task
    12): a ranking-relevant settings change is only worth re-running the
    suite for when the workspace has at least one golden query authored."""
    row = (
        await session.execute(
            select(GoldenQuery.id).where(GoldenQuery.workspace_id == workspace_id).limit(1)
        )
    ).first()
    return row is not None


async def latest_eval_run_per_workspace(session: AsyncSession, org_id: UUID) -> list[EvalRun]:
    """Dashboard eval-trend table (Task 12, §6): the most recent EvalRun per
    workspace in the org, newest-first. Org-scoped via the Workspace join
    (mirrors list_eval_runs above). workspace_name is attached as an extra,
    unmapped attribute -- not persisted, just carried on the returned ORM
    instance -- so the route's EvalTrendOut.model_validate(from_attributes)
    can render the workspace by name instead of a bare id."""
    latest = (
        select(EvalRun.workspace_id, func.max(EvalRun.created_at).label("created_at"))
        .join(Workspace, Workspace.id == EvalRun.workspace_id)
        .where(Workspace.org_id == org_id)
        .group_by(EvalRun.workspace_id)
        .subquery()
    )
    rows = (
        await session.execute(
            select(EvalRun, Workspace.name)
            .join(
                latest,
                (EvalRun.workspace_id == latest.c.workspace_id)
                & (EvalRun.created_at == latest.c.created_at),
            )
            .join(Workspace, Workspace.id == EvalRun.workspace_id)
            .order_by(EvalRun.created_at.desc())
        )
    ).all()
    runs = []
    for run, workspace_name in rows:
        run.workspace_name = workspace_name
        runs.append(run)
    return runs


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
