from collections.abc import Mapping
from uuid import UUID

from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ragz.core.app_settings import get_app_setting
from ragz.core.errors import ConflictError, NotFoundError, WorkspaceAccessDenied
from ragz.modules.audit.service import record_audit
from ragz.modules.auth.models import User
from ragz.modules.models import service as models_service
from ragz.modules.tenancy.context import TenantContext
from ragz.modules.tenancy.models import (
    Group,
    Organization,
    RoleTemplate,
    RoleTemplateVersion,
    UserGroup,
    Workspace,
    WorkspaceMember,
)
from ragz.modules.tenancy.permissions import PERMISSIONS


async def create_workspace(session: AsyncSession, ctx: TenantContext, name: str) -> Workspace:
    # New workspaces inherit the superadmin-global default chunking strategy
    # (settings_service._DEFAULT_CHUNK_METHOD_KEY). Existing workspaces are
    # untouched; each workspace's chunk_method stays independently editable in
    # Workspace Settings. Unset -> "heading" (the column default).
    default_chunk = await get_app_setting(session, "default_chunk_method") or "heading"
    ws = Workspace(org_id=ctx.org_id, name=name, chunk_method=default_chunk)
    session.add(ws)
    await session.flush()
    await record_audit(session, org_id=ctx.org_id, actor_id=ctx.user_id,
                       action="workspace.created", target_type="workspace",
                       target_id=str(ws.id))
    await session.commit()
    return ws


async def list_workspaces(session: AsyncSession, ctx: TenantContext) -> list[Workspace]:
    stmt = select(Workspace).where(Workspace.org_id == ctx.org_id)
    if ctx.role == "user":
        stmt = stmt.where(Workspace.id.in_(ctx.workspace_ids))
    return list((await session.execute(stmt.order_by(Workspace.name))).scalars())


async def add_member(
    session: AsyncSession, ctx: TenantContext, workspace_id: UUID, user_id: UUID, role: str
) -> None:
    ws = (
        await session.execute(
            select(Workspace).where(Workspace.id == workspace_id, Workspace.org_id == ctx.org_id)
        )
    ).scalar_one_or_none()
    if ws is None:
        raise NotFoundError("workspace not found")
    user = (
        await session.execute(select(User).where(User.id == user_id, User.org_id == ctx.org_id))
    ).scalar_one_or_none()
    if user is None:
        raise NotFoundError("user not found")
    session.add(WorkspaceMember(workspace_id=workspace_id, user_id=user_id, role=role))
    await record_audit(session, org_id=ctx.org_id, actor_id=ctx.user_id,
                       action="workspace.member_added", target_type="workspace_member",
                       target_id=f"{workspace_id}:{user_id}")
    await session.commit()


async def list_members(
    session: AsyncSession, ctx: TenantContext, workspace_id: UUID
) -> list[WorkspaceMember]:
    await get_workspace_checked(session, ctx, workspace_id)
    stmt = select(WorkspaceMember).where(WorkspaceMember.workspace_id == workspace_id)
    return list((await session.execute(stmt)).scalars())


async def _is_last_owner(
    session: AsyncSession, workspace_id: UUID, user_id: UUID
) -> bool:
    owners = (
        await session.execute(
            select(WorkspaceMember.user_id).where(
                WorkspaceMember.workspace_id == workspace_id, WorkspaceMember.role == "owner",
            )
        )
    ).scalars().all()
    return list(owners) == [user_id]


async def change_member_role(
    session: AsyncSession, ctx: TenantContext, workspace_id: UUID, user_id: UUID, role: str,
) -> WorkspaceMember:
    await get_workspace_checked(session, ctx, workspace_id)
    member = await session.get(WorkspaceMember, (workspace_id, user_id))
    if member is None:
        raise NotFoundError("member not found")
    if role != "owner" and await _is_last_owner(session, workspace_id, user_id):
        raise ConflictError("cannot demote the workspace's last owner")
    member.role = role
    await record_audit(session, org_id=ctx.org_id, actor_id=ctx.user_id,
                       action="workspace.member_role_changed", target_type="workspace_member",
                       target_id=f"{workspace_id}:{user_id}")
    await session.commit()
    return member


async def remove_member(
    session: AsyncSession, ctx: TenantContext, workspace_id: UUID, user_id: UUID,
) -> None:
    await get_workspace_checked(session, ctx, workspace_id)
    member = await session.get(WorkspaceMember, (workspace_id, user_id))
    if member is None:
        raise NotFoundError("member not found")
    if await _is_last_owner(session, workspace_id, user_id):
        raise ConflictError("cannot remove the workspace's last owner")
    await session.delete(member)
    # RBAC-08: no separate "revoke dependent access" step is needed here --
    # get_tenant_context/build_context_for_user already reload workspace_ids
    # fresh from WorkspaceMember on EVERY human request, and
    # build_verified_principal_context (RBAC-02) already re-validates current
    # membership on EVERY API-key/bot request. Deleting this row is already
    # the complete, immediate revocation; Task 12 adds the adversarial proof.
    await record_audit(session, org_id=ctx.org_id, actor_id=ctx.user_id,
                       action="workspace.member_removed", target_type="workspace_member",
                       target_id=f"{workspace_id}:{user_id}")
    await session.commit()


async def get_workspace_checked(
    session: AsyncSession, ctx: TenantContext, workspace_id: UUID
) -> Workspace:
    """The one workspace-access gate used by documents and retrieval (iron rule 1's
    Postgres-side counterpart). Same 403 for cross-org and non-member so existence
    never leaks."""
    ws = (
        await session.execute(
            select(Workspace).where(Workspace.id == workspace_id, Workspace.org_id == ctx.org_id)
        )
    ).scalar_one_or_none()
    if ws is None:
        raise WorkspaceAccessDenied("workspace not found or not accessible")
    if ctx.role == "user" and workspace_id not in ctx.workspace_ids:
        raise WorkspaceAccessDenied("workspace not found or not accessible")
    return ws


async def get_workspace(
    session: AsyncSession, ctx: TenantContext, workspace_id: UUID
) -> Workspace:
    ws = (
        await session.execute(
            select(Workspace).where(Workspace.id == workspace_id, Workspace.org_id == ctx.org_id)
        )
    ).scalar_one_or_none()
    if ws is None or (ctx.role == "user" and workspace_id not in ctx.workspace_ids):
        raise NotFoundError("workspace not found")
    return ws


async def set_default_model(
    session: AsyncSession, ctx: TenantContext, workspace_id: UUID, model_id: UUID | None,
    *, commit: bool = True
) -> Workspace:
    ws = await get_workspace(session, ctx, workspace_id)
    if model_id is not None:
        await models_service.get_model(session, model_id)  # NotFoundError if unknown
    ws.default_model_id = model_id
    if commit:
        await session.commit()
    else:
        await session.flush()
    return ws


async def workspace_uses_embedding_model(session: AsyncSession, model_id: UUID) -> bool:
    """DOC-10 delete guard (models/service.py::delete_model): mirrors
    delete_role_template's "assigned to at least one X" 409 check."""
    row = (
        await session.execute(
            select(Workspace.id).where(Workspace.embedding_model_id == model_id).limit(1)
        )
    ).first()
    return row is not None


async def set_embedding_model(
    session: AsyncSession, ctx: TenantContext, workspace_id: UUID, model_id: UUID
) -> Workspace:
    """DOC-10 lock-after-first-index: switches immediately if the workspace
    has zero indexed documents; 409s (pointing at the re-embed flow) if it
    has any. Local import: documents.service already imports THIS module
    (get_workspace_checked) at module scope, so importing documents.service
    here at module scope would be circular -- same sanctioned shape as this
    file's existing worker.tasks / evals.service local imports."""
    from ragz.modules.documents.service import has_indexed_documents
    from ragz.modules.models import service as models_service

    ws = await get_workspace_checked(session, ctx, workspace_id)
    model = await models_service.get_model(session, model_id)
    if model.modality != "embedding":
        raise ConflictError("model is not an embedding model")
    if await has_indexed_documents(session, ctx, workspace_id):
        raise ConflictError(
            "workspace already has indexed documents -- use the re-embed job to switch models"
        )
    ws.embedding_model_id = model_id
    await record_audit(session, org_id=ctx.org_id, actor_id=ctx.user_id,
                       action="workspace.embedding_model_changed", target_type="workspace",
                       target_id=str(ws.id))
    await session.commit()
    return ws


_RETRIEVAL_SETTINGS_FIELDS = {
    "top_k", "min_score", "rerank_enabled", "system_prompt_override", "fallback_policy",
    "web_search_enabled", "strict_mode", "enrichment_enabled", "chunk_method",
    "generative_ui_enabled",
}

# Task 12 (§6): the subset of _RETRIEVAL_SETTINGS_FIELDS that actually changes
# what gets retrieved/ranked -- NOT fallback_policy/strict_mode/
# web_search_enabled/system_prompt_override/enrichment_enabled/
# generative_ui_enabled, which affect generation-time or ingestion-time
# behavior, not retrieval itself.
_RANKING_FIELDS = {"top_k", "min_score", "rerank_enabled"}


async def update_retrieval_settings(
    session: AsyncSession, ctx: TenantContext, workspace_id: UUID,
    updates: Mapping[str, object], *, commit: bool = True
) -> Workspace:
    """ADM-3 tuning knobs. `system_prompt_override` is the only nullable field —
    explicit null clears it; null for any other field is a 409."""
    ws = await get_workspace(session, ctx, workspace_id)
    for field, value in updates.items():
        if field not in _RETRIEVAL_SETTINGS_FIELDS:
            raise ConflictError(f"not a retrieval setting: {field}")
        if value is None and field != "system_prompt_override":
            raise ConflictError(f"{field} cannot be null")
        setattr(ws, field, value)
    await record_audit(session, org_id=ctx.org_id, actor_id=ctx.user_id,
                       action="workspace.retrieval_settings_changed",
                       target_type="workspace", target_id=str(ws.id))
    if commit:
        await session.commit()
    else:
        await session.flush()
    if _RANKING_FIELDS & updates.keys():
        # Task 12 (§6): a ranking-relevant change re-runs the eval suite (if
        # any golden query exists) so admins see the impact without waiting
        # for the nightly job. Local imports break a REAL circular import
        # (evals.service already imports tenancy.service at module scope;
        # worker.tasks -> ... -> tenancy.service transitively too) -- see
        # this module's ignore_imports entry in pyproject.toml's layering
        # contract, mirroring documents/service.py's enqueue_reindex
        # precedent. The enqueue is a fire-and-forget Celery apply_async
        # (never raises against a healthy broker) placed after the write
        # above (commit or flush) rather than gated on `commit` itself:
        # PATCH /workspaces calls this with commit=False and commits the
        # SAME session immediately afterward in the same request, so there
        # is no meaningful window in which a rolled-back write could still
        # have scheduled a run.
        from ragz.modules.evals.service import has_any_golden_query
        from ragz.worker.tasks import enqueue_eval_run

        if await has_any_golden_query(session, workspace_id):
            enqueue_eval_run(workspace_id, "settings_change")
    return ws


async def create_group(session: AsyncSession, ctx: TenantContext, name: str) -> Group:
    group = Group(org_id=ctx.org_id, name=name)
    session.add(group)
    await session.flush()
    await record_audit(session, org_id=ctx.org_id, actor_id=ctx.user_id,
                       action="group.created", target_type="group", target_id=str(group.id))
    await session.commit()
    return group


async def _org_group(session: AsyncSession, ctx: TenantContext, group_id: UUID) -> Group:
    group = (
        await session.execute(
            select(Group).where(Group.id == group_id, Group.org_id == ctx.org_id)
        )
    ).scalar_one_or_none()
    if group is None:
        raise NotFoundError("group not found")
    return group


async def list_groups(
    session: AsyncSession, ctx: TenantContext
) -> list[tuple[Group, list[UUID]]]:
    groups = list(
        (
            await session.execute(
                select(Group).where(Group.org_id == ctx.org_id).order_by(Group.name)
            )
        ).scalars()
    )
    rows = (
        await session.execute(
            select(UserGroup.group_id, UserGroup.user_id)
            .join(Group, Group.id == UserGroup.group_id)
            .where(Group.org_id == ctx.org_id)
        )
    ).all()
    members: dict[UUID, list[UUID]] = {g.id: [] for g in groups}
    for group_id, user_id in rows:
        members[group_id].append(user_id)
    return [(g, sorted(members[g.id])) for g in groups]


async def delete_group(session: AsyncSession, ctx: TenantContext, group_id: UUID) -> None:
    group = await _org_group(session, ctx, group_id)
    await session.delete(group)  # user_groups rows cascade at the DB layer
    await record_audit(session, org_id=ctx.org_id, actor_id=ctx.user_id,
                       action="group.deleted", target_type="group", target_id=str(group_id))
    await session.commit()


async def add_group_member(
    session: AsyncSession, ctx: TenantContext, group_id: UUID, user_id: UUID
) -> None:
    await _org_group(session, ctx, group_id)
    user = (
        await session.execute(select(User).where(User.id == user_id, User.org_id == ctx.org_id))
    ).scalar_one_or_none()
    if user is None:
        raise NotFoundError("user not found")
    session.add(UserGroup(group_id=group_id, user_id=user_id))
    await record_audit(session, org_id=ctx.org_id, actor_id=ctx.user_id,
                       action="group.member_added", target_type="group",
                       target_id=f"{group_id}:{user_id}")
    await session.commit()


async def remove_group_member(
    session: AsyncSession, ctx: TenantContext, group_id: UUID, user_id: UUID
) -> None:
    await _org_group(session, ctx, group_id)
    await session.execute(
        sa_delete(UserGroup).where(
            UserGroup.group_id == group_id, UserGroup.user_id == user_id
        )
    )
    await record_audit(session, org_id=ctx.org_id, actor_id=ctx.user_id,
                       action="group.member_removed", target_type="group",
                       target_id=f"{group_id}:{user_id}")
    await session.commit()


async def list_organizations(session: AsyncSession) -> list[Organization]:
    """Superadmin-only listing (admin/sso routes) — platform-wide, not org-scoped."""
    return list(
        (await session.execute(select(Organization).order_by(Organization.name))).scalars()
    )


async def set_org_sso_domains(
    session: AsyncSession, *, actor_id: UUID | None, org_id: UUID, domains: list[str]
) -> Organization:
    """AUTH-6: normalize to lowercase, de-duplicate, empty collapses to None.

    Review finding: a domain must be claimed by at most one org, otherwise a
    JIT-provisioned SSO user gets routed into whichever org happens to sort
    first (see modules/auth/service.py:login_oidc). Reject the write (409) if
    any of the requested domains is already claimed by a DIFFERENT org; the
    same org re-claiming (or extending) its own list is fine.
    """
    org = (
        await session.execute(select(Organization).where(Organization.id == org_id))
    ).scalar_one_or_none()
    if org is None:
        raise NotFoundError("organization not found")
    normalized = sorted({d.strip().lower() for d in domains if d.strip()})
    if normalized:
        conflict = (
            await session.execute(
                select(Organization.id).where(
                    Organization.id != org_id,
                    Organization.sso_domains.overlap(normalized),
                )
            )
        ).first()
        if conflict is not None:
            raise ConflictError("one or more domains are already claimed by another organization")
    org.sso_domains = normalized or None
    await record_audit(session, org_id=org.id, actor_id=actor_id,
                       action="org.sso_domains_changed", target_type="organization",
                       target_id=str(org.id))
    await session.commit()
    return org


def _check_known_permissions(permissions: list[str]) -> None:
    unknown = sorted(set(permissions) - PERMISSIONS)
    if unknown:
        raise ConflictError(f"unknown permission flag(s): {', '.join(unknown)}")


async def list_role_templates(session: AsyncSession) -> list[RoleTemplate]:
    """Role templates are GLOBAL (no org_id, superadmin-built) -- every org
    admin sees the same list to assign from."""
    return list(
        (await session.execute(select(RoleTemplate).order_by(RoleTemplate.name))).scalars()
    )


async def create_role_template(
    session: AsyncSession, ctx: TenantContext, *, name: str, description: str,
    permissions: list[str],
) -> RoleTemplate:
    _check_known_permissions(permissions)
    template = RoleTemplate(name=name, description=description, permissions=permissions)
    session.add(template)
    await session.flush()
    await record_audit(session, org_id=None, actor_id=ctx.user_id,
                       action="role_template.created", target_type="role_template",
                       target_id=str(template.id))
    await session.commit()
    return template


async def _get_role_template(session: AsyncSession, role_template_id: UUID) -> RoleTemplate:
    template = (
        await session.execute(
            select(RoleTemplate).where(RoleTemplate.id == role_template_id)
        )
    ).scalar_one_or_none()
    if template is None:
        raise NotFoundError("role template not found")
    return template


async def update_role_template(
    session: AsyncSession, ctx: TenantContext, role_template_id: UUID, *,
    name: str | None = None, description: str | None = None,
    permissions: list[str] | None = None,
) -> RoleTemplate:
    template = await _get_role_template(session, role_template_id)
    if permissions is not None:
        _check_known_permissions(permissions)
        template.permissions = permissions
    if name is not None:
        template.name = name
    if description is not None:
        template.description = description
    await record_audit(session, org_id=None, actor_id=ctx.user_id,
                       action="role_template.updated", target_type="role_template",
                       target_id=str(template.id))
    await session.commit()
    return template


async def activate_role_template(
    session: AsyncSession, ctx: TenantContext, role_template_id: UUID
) -> RoleTemplate:
    """RBAC-09: flips a draft (or re-activates an archived) template to
    active and bumps its version -- the only way a new template becomes
    assignable via assign_custom_role. Also writes an immutable
    RoleTemplateVersion snapshot of the permissions being activated -- the
    history rollback_role_template reads from."""
    template = await _get_role_template(session, role_template_id)
    template.status = "active"
    template.version += 1
    session.add(
        RoleTemplateVersion(
            role_template_id=template.id, version=template.version,
            permissions=list(template.permissions),
        )
    )
    await record_audit(session, org_id=None, actor_id=ctx.user_id,
                       action="role_template.activated", target_type="role_template",
                       target_id=f"{template.id}@v{template.version}")
    await session.commit()
    return template


async def rollback_role_template(
    session: AsyncSession, ctx: TenantContext, role_template_id: UUID
) -> RoleTemplate:
    """RBAC-09: restores the PREVIOUS version snapshot's permissions and
    activates that as a NEW forward version -- rollback never rewrites
    role_template_versions history, it only changes what the CURRENT
    role_templates.permissions row holds and appends yet another snapshot
    on top (via activate_role_template)."""
    template = await _get_role_template(session, role_template_id)
    versions = list(
        (
            await session.execute(
                select(RoleTemplateVersion)
                .where(RoleTemplateVersion.role_template_id == role_template_id)
                .order_by(RoleTemplateVersion.version.desc())
                .limit(2)
            )
        ).scalars()
    )
    if len(versions) < 2:
        raise ConflictError("no previous version to roll back to")
    previous = versions[1]
    template.permissions = list(previous.permissions)
    return await activate_role_template(session, ctx, role_template_id)


async def role_template_impact(session: AsyncSession, role_template_id: UUID) -> int:
    """RBAC-09: count of users currently assigned this template, for the
    superadmin/admin "impact preview" before activating/editing/deleting."""
    return (
        await session.execute(
            select(func.count()).select_from(User).where(User.custom_role_id == role_template_id)
        )
    ).scalar_one()


async def delete_role_template(
    session: AsyncSession, ctx: TenantContext, role_template_id: UUID
) -> None:
    template = await _get_role_template(session, role_template_id)
    assigned = (
        await session.execute(
            select(User.id).where(User.custom_role_id == role_template_id).limit(1)
        )
    ).first()
    if assigned is not None:
        raise ConflictError("role template is assigned to at least one user")
    await session.delete(template)
    await record_audit(session, org_id=None, actor_id=ctx.user_id,
                       action="role_template.deleted", target_type="role_template",
                       target_id=str(role_template_id))
    await session.commit()


async def assign_custom_role(
    session: AsyncSession, ctx: TenantContext, user_id: UUID, role_template_id: UUID | None
) -> User:
    """AdminDep-gated (RBAC-2/RBAC-05): target must be same-org. Superadmin
    targets are still rejected (platform-tier, out of this org-scoped
    mechanism's reach) -- 404 so existence never leaks. 'user' AND 'admin'
    tier targets are both allowed as of RBAC-05: an org admin now needs an
    EXPLICIT template (e.g. Content Manager) for content-ACL bypass or audit
    access, exactly like a 'user'-tier account needs one for upload/delete.
    Cross-org targets 404 (existence never leaks, matching _org_user)."""
    user = (
        await session.execute(
            select(User).where(User.id == user_id, User.org_id == ctx.org_id)
        )
    ).scalar_one_or_none()
    if user is None or user.role == "superadmin":
        raise NotFoundError("user not found")
    if role_template_id is not None:
        template = await _get_role_template(session, role_template_id)  # NotFoundError if unknown
        if template.status != "active":
            raise ConflictError("role template is not active")
    user.custom_role_id = role_template_id
    action = (
        "user.custom_role_assigned" if role_template_id is not None else "user.custom_role_cleared"
    )
    await record_audit(session, org_id=ctx.org_id, actor_id=ctx.user_id,
                       action=action, target_type="user", target_id=str(user_id))
    await session.commit()
    return user
