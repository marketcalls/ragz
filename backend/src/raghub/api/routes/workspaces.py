from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from raghub.api.deps import get_session
from raghub.modules.tenancy import service
from raghub.modules.tenancy.context import (
    TenantContext,
    get_tenant_context,
    require_permission,
    require_role,
)
from raghub.modules.tenancy.schemas import (
    MemberAdd,
    WorkspaceCreate,
    WorkspaceOut,
    WorkspacePatch,
)
from raghub.worker.tasks import enqueue_enrichment_backfill

router = APIRouter(prefix="/workspaces", tags=["workspaces"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
CtxDep = Annotated[TenantContext, Depends(get_tenant_context)]
AdminDep = Annotated[TenantContext, Depends(require_role("admin"))]
# Task 13 (RBAC-2): PATCH (settings) is workspace configuration, distinct from
# org administration (POST /workspaces, POST .../members stay AdminDep).
ConfigureDep = Annotated[TenantContext, Depends(require_permission("workspace.configure"))]


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


_SETTINGS_FIELDS = (
    "top_k", "min_score", "rerank_enabled", "system_prompt_override", "fallback_policy",
    "web_search_enabled", "strict_mode", "enrichment_enabled",
)


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
