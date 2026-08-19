from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ragz.api.deps import get_session
from ragz.core.errors import NotFoundError
from ragz.modules.tenancy import service
from ragz.modules.tenancy.context import TenantContext, require_action
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
from ragz.modules.tenancy.views import ReembedJobView
from ragz.worker.tasks import enqueue_enrichment_backfill, enqueue_reembed_workspace

router = APIRouter(prefix="/workspaces", tags=["workspaces"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
# Task 13 (RBAC-2): PATCH (settings) is workspace configuration, distinct from
# org administration (POST /workspaces, POST .../members).
ConfigureDep = Annotated[TenantContext, Depends(require_action("workspace.configure"))]
# Task 11 (RBAC-08): member list/change-role/remove — distinct from
# workspace.create/POST-member (org administration, but its own granular
# action per sec RAGZ-PUB-01b below).
MembersReadDep = Annotated[TenantContext, Depends(require_action("workspace.members.read"))]
MembersManageDep = Annotated[TenantContext, Depends(require_action("workspace.members.manage"))]
# sec RAGZ-PUB-01b: both DECLARE their action in api/policy.py
# (workspace.create / workspace.members.manage) but were still gated on
# require_role("admin") -- converted so a custom role granted the specific
# action (without the "admin" role tier) can use them, and so
# audit_route_enforcement's dependency-graph walk finds the declared action
# actually wired.
CreateDep = Annotated[TenantContext, Depends(require_action("workspace.create"))]
# sec RAGZ-PUB-01: GET /workspaces and GET .../reembed-status DECLARE
# workspace.read but were auth-only (CtxDep); POST .../reembed DECLARES
# workspace.reembed but enforced workspace.configure. Enforce the declared
# action for each. (The workspace-membership fence still lives in the service
# layer -- list_workspaces / get_workspace -- as defense in depth.)
ReadDep = Annotated[TenantContext, Depends(require_action("workspace.read"))]
ReembedDep = Annotated[TenantContext, Depends(require_action("workspace.reembed"))]


@router.post("", status_code=201, response_model=WorkspaceOut)
async def create(body: WorkspaceCreate, session: SessionDep, ctx: CreateDep) -> WorkspaceOut:
    ws = await service.create_workspace(session, ctx, body.name)
    return WorkspaceOut.model_validate(ws)


@router.get("", response_model=list[WorkspaceOut])
async def list_(session: SessionDep, ctx: ReadDep) -> list[WorkspaceOut]:
    return [WorkspaceOut.model_validate(w) for w in await service.list_workspaces(session, ctx)]


@router.post("/{workspace_id}/members", status_code=204)
async def add_member(
    workspace_id: UUID, body: MemberAdd, session: SessionDep, ctx: MembersManageDep
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
    "generative_ui_enabled",
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
    workspace_id: UUID, body: ReembedRequest, session: SessionDep, ctx: ReembedDep
) -> ReembedJobOut:
    """Admin-confirmed switch for a workspace that already has indexed
    content (the 409 path of PATCH .../embedding-model points here).

    The job's lifecycle -- creating the row synchronously so the upload guard
    is armed before this returns, and closing it if the enqueue fails -- lives
    in tenancy's service, which owns the table (Phase 2 item 1). The task
    publisher is passed in because a domain module must not know Celery
    exists; this route is the layer that may.
    """
    job = await service.start_reembed_job(
        session, ctx, workspace_id, body.new_embedding_model_id,
        enqueue=enqueue_reembed_workspace,
    )
    return _reembed_out(job)


def _reembed_out(job: ReembedJobView) -> ReembedJobOut:
    """One serialiser for both reembed routes, which had identical bodies."""
    return ReembedJobOut(
        id=job.id, workspace_id=job.workspace_id,
        old_embedding_model_id=job.old_embedding_model_id,
        new_embedding_model_id=job.new_embedding_model_id,
        documents_total=job.documents_total, documents_done=job.documents_done,
        error=job.error, finished_at=job.finished_at.isoformat() if job.finished_at else None,
    )


@router.get("/{workspace_id}/reembed-status", response_model=ReembedJobOut)
async def get_reembed_status(
    workspace_id: UUID, session: SessionDep, ctx: ReadDep
) -> ReembedJobOut:
    await service.get_workspace(session, ctx, workspace_id)  # 404s if not accessible
    job = await service.get_latest_reembed_job(session, workspace_id)
    if job is None:
        raise NotFoundError("no re-embed job for this workspace")
    return _reembed_out(job)
