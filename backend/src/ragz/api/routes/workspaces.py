from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ragz.api.deps import get_session
from ragz.core.db import naive_utc
from ragz.core.errors import ConflictError, NotFoundError
from ragz.modules.models import service as models_service
from ragz.modules.tenancy import service
from ragz.modules.tenancy.context import (
    TenantContext,
    get_tenant_context,
    require_action,
    require_role,
)
from ragz.modules.tenancy.reembed_models import ReembedJob
from ragz.modules.tenancy.schemas import (
    EmbeddingModelPatch,
    MemberAdd,
    MemberOut,
    MemberRolePatch,
    ReembedJobOut,
    ReembedRequest,
    WorkspaceCreate,
    WorkspaceOut,
    WorkspacePatch,
)
from ragz.worker.tasks import enqueue_enrichment_backfill, enqueue_reembed_workspace

router = APIRouter(prefix="/workspaces", tags=["workspaces"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
CtxDep = Annotated[TenantContext, Depends(get_tenant_context)]
AdminDep = Annotated[TenantContext, Depends(require_role("admin"))]
# Task 13 (RBAC-2): PATCH (settings) is workspace configuration, distinct from
# org administration (POST /workspaces, POST .../members stay AdminDep).
ConfigureDep = Annotated[TenantContext, Depends(require_action("workspace.configure"))]
# Task 11 (RBAC-08): member list/change-role/remove — distinct from
# workspace.create/POST-member (AdminDep, org administration).
MembersReadDep = Annotated[TenantContext, Depends(require_action("workspace.members.read"))]
MembersManageDep = Annotated[TenantContext, Depends(require_action("workspace.members.manage"))]


@router.post("", status_code=201, response_model=WorkspaceOut)
async def create(body: WorkspaceCreate, session: SessionDep, ctx: AdminDep) -> WorkspaceOut:
    ws = await service.create_workspace(session, ctx, body.name)
    return WorkspaceOut.model_validate(ws)


@router.get("", response_model=list[WorkspaceOut])
async def list_(session: SessionDep, ctx: CtxDep) -> list[WorkspaceOut]:
    return [WorkspaceOut.model_validate(w) for w in await service.list_workspaces(session, ctx)]


@router.post("/{workspace_id}/members", status_code=204)
async def add_member(
    workspace_id: UUID, body: MemberAdd, session: SessionDep, ctx: AdminDep
) -> None:
    await service.add_member(session, ctx, workspace_id, body.user_id, body.role)


@router.get("/{workspace_id}/members", response_model=list[MemberOut])
async def list_members_route(
    workspace_id: UUID, session: SessionDep, ctx: MembersReadDep
) -> list[MemberOut]:
    return [
        MemberOut.model_validate(m) for m in await service.list_members(session, ctx, workspace_id)
    ]


@router.patch("/{workspace_id}/members/{user_id}", response_model=MemberOut)
async def change_member_role_route(
    workspace_id: UUID, user_id: UUID, body: MemberRolePatch,
    session: SessionDep, ctx: MembersManageDep,
) -> MemberOut:
    member = await service.change_member_role(session, ctx, workspace_id, user_id, body.role)
    return MemberOut.model_validate(member)


@router.delete("/{workspace_id}/members/{user_id}", status_code=204)
async def remove_member_route(
    workspace_id: UUID, user_id: UUID, session: SessionDep, ctx: MembersManageDep,
) -> None:
    await service.remove_member(session, ctx, workspace_id, user_id)


_SETTINGS_FIELDS = (
    "top_k", "min_score", "rerank_enabled", "system_prompt_override", "fallback_policy",
    "web_search_enabled", "strict_mode", "enrichment_enabled", "chunk_method",
)


@router.patch("/{workspace_id}/embedding-model", response_model=WorkspaceOut)
async def patch_embedding_model(
    workspace_id: UUID, body: EmbeddingModelPatch, session: SessionDep, ctx: ConfigureDep
) -> WorkspaceOut:
    ws = await service.set_embedding_model(session, ctx, workspace_id, body.embedding_model_id)
    return WorkspaceOut.model_validate(ws)


@router.patch("/{workspace_id}", response_model=WorkspaceOut)
async def patch_workspace(
    workspace_id: UUID, body: WorkspacePatch, session: SessionDep, ctx: ConfigureDep
) -> WorkspaceOut:
    ws = None
    has_changes = False
    if "default_model_id" in body.model_fields_set:
        ws = await service.set_default_model(
            session, ctx, workspace_id, body.default_model_id, commit=False
        )
        has_changes = True
    updates = {
        f: getattr(body, f) for f in _SETTINGS_FIELDS if f in body.model_fields_set
    }
    # Task 7 (Plan K §4): capture the PRE-update value before
    # update_retrieval_settings mutates the same session-identity-mapped
    # workspace row via setattr — reading it afterwards would only ever see
    # the new value, making a False->True transition indistinguishable from
    # an already-True no-op PATCH.
    was_enrichment_enabled: bool | None = None
    if updates.get("enrichment_enabled") is True:
        was_enrichment_enabled = (
            await service.get_workspace(session, ctx, workspace_id)
        ).enrichment_enabled
    if updates:
        ws = await service.update_retrieval_settings(
            session, ctx, workspace_id, updates, commit=False
        )
        has_changes = True
    if has_changes:
        await session.commit()
    if ws is None:
        ws = await service.get_workspace(session, ctx, workspace_id)
    # Backfill only on a genuine False->True transition (spec §4) — a
    # redundant `{"enrichment_enabled": true}` PATCH when it's already true
    # must not re-enqueue.
    if was_enrichment_enabled is False:
        enqueue_enrichment_backfill(workspace_id)
    return WorkspaceOut.model_validate(ws)


@router.post("/{workspace_id}/reembed", status_code=202, response_model=ReembedJobOut)
async def start_reembed(
    workspace_id: UUID, body: ReembedRequest, session: SessionDep, ctx: ConfigureDep
) -> ReembedJobOut:
    """Admin-confirmed switch for a workspace that already has indexed
    content (the 409 path of PATCH .../embedding-model points here).

    Fix round 2: creates the ReembedJob row SYNCHRONOUSLY, with started_at
    set, and commits it in this request's own transaction BEFORE enqueueing
    the Celery task -- not inside run_reembed_workspace as before. That
    closes the race described in
    .superpowers/sdd/final-review-fix-report.md: previously the row only
    came into existence once Celery actually picked up the task, so
    documents/service.py::create_from_upload's in-progress guard saw NO job
    at all during the enqueue-to-pickup gap and let uploads through that
    could then be silently wiped by the re-embed's workspace-wide delete.
    Creating the row here means the guard is armed from the instant this
    response returns to the admin -- no window. documents_total is 0 here
    (the real count isn't known until run_reembed_workspace counts the
    workspace's documents) and gets updated on this same row once it does."""
    ws = await service.get_workspace(session, ctx, workspace_id)
    if body.new_embedding_model_id == ws.embedding_model_id:
        # Guards against a double-submit/retry re-requesting the model the
        # workspace already uses: run_reembed_workspace's old/new collection
        # would be identical, and its post-upsert "delete from OLD
        # collection" step would then delete every point it just wrote,
        # silently wiping the workspace's vectors.
        raise ConflictError("workspace is already using this embedding model")
    new_model = await models_service.get_model(session, body.new_embedding_model_id)
    if new_model.modality != "embedding":
        raise ConflictError("model is not an embedding model")
    job = ReembedJob(
        workspace_id=workspace_id, old_embedding_model_id=ws.embedding_model_id,
        new_embedding_model_id=body.new_embedding_model_id, documents_total=0,
        started_at=naive_utc(),
    )
    session.add(job)
    await session.commit()
    try:
        enqueue_reembed_workspace(workspace_id, job.id, body.new_embedding_model_id)
    except Exception as exc:
        # Fix round 3: the job row above is already committed (started_at
        # set), so if the enqueue call itself blows up (e.g. the Celery
        # broker/Redis is down), the job would otherwise be left "in
        # progress" forever with no task ever running to close it --
        # create_from_upload's guard would then permanently reject uploads
        # to this workspace. Close it here before letting the error surface.
        job.error = str(exc)[:1000]
        job.finished_at = naive_utc()
        await session.commit()
        raise
    return ReembedJobOut(
        id=job.id, workspace_id=job.workspace_id,
        old_embedding_model_id=job.old_embedding_model_id,
        new_embedding_model_id=job.new_embedding_model_id,
        documents_total=job.documents_total, documents_done=job.documents_done,
        error=job.error, finished_at=job.finished_at.isoformat() if job.finished_at else None,
    )


@router.get("/{workspace_id}/reembed-status", response_model=ReembedJobOut)
async def get_reembed_status(
    workspace_id: UUID, session: SessionDep, ctx: CtxDep
) -> ReembedJobOut:
    await service.get_workspace(session, ctx, workspace_id)  # 404s if not accessible
    job = (
        await session.execute(
            select(ReembedJob)
            .where(ReembedJob.workspace_id == workspace_id)
            .order_by(ReembedJob.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if job is None:
        raise NotFoundError("no re-embed job for this workspace")
    return ReembedJobOut(
        id=job.id, workspace_id=job.workspace_id,
        old_embedding_model_id=job.old_embedding_model_id,
        new_embedding_model_id=job.new_embedding_model_id,
        documents_total=job.documents_total, documents_done=job.documents_done,
        error=job.error, finished_at=job.finished_at.isoformat() if job.finished_at else None,
    )
