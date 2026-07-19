from collections.abc import Mapping
from uuid import UUID

from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from raghub.core.errors import ConflictError, NotFoundError, WorkspaceAccessDenied
from raghub.modules.audit.service import record_audit
from raghub.modules.auth.models import User
from raghub.modules.models import service as models_service
from raghub.modules.tenancy.context import TenantContext
from raghub.modules.tenancy.models import (
    Group,
    Organization,
    RoleTemplate,
    UserGroup,
    Workspace,
    WorkspaceMember,
)
from raghub.modules.tenancy.permissions import PERMISSIONS


async def create_workspace(session: AsyncSession, ctx: TenantContext, name: str) -> Workspace:
    ws = Workspace(org_id=ctx.org_id, name=name)
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


_RETRIEVAL_SETTINGS_FIELDS = {
    "top_k", "min_score", "rerank_enabled", "system_prompt_override", "fallback_policy",
    "web_search_enabled",
}


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
    """AdminDep-gated (RBAC-2): target must be same-org and role == "user" --
    assigning a custom role to an admin/superadmin is meaningless (they already
    hold every permission), so that's a 409, not silently accepted. Cross-org
    targets 404 (existence never leaks, matching _org_user elsewhere)."""
    user = (
        await session.execute(
            select(User).where(User.id == user_id, User.org_id == ctx.org_id)
        )
    ).scalar_one_or_none()
    if user is None or user.role == "superadmin":
        raise NotFoundError("user not found")
    if user.role != "user":
        raise ConflictError("custom roles apply to 'user'-tier accounts only")
    if role_template_id is not None:
        await _get_role_template(session, role_template_id)  # NotFoundError if unknown
    user.custom_role_id = role_template_id
    action = (
        "user.custom_role_assigned" if role_template_id is not None else "user.custom_role_cleared"
    )
    await record_audit(session, org_id=ctx.org_id, actor_id=ctx.user_id,
                       action=action, target_type="user", target_id=str(user_id))
    await session.commit()
    return user
