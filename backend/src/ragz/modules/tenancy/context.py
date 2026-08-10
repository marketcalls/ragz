from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

import structlog
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ragz.core.app_settings import get_or_create_signing_key
from ragz.core.db import get_session
from ragz.core.errors import AuthenticationError, AuthorizationError
from ragz.core.ratelimit import check_rate_limit
from ragz.modules.auth.models import User
from ragz.modules.auth.tokens import decode_access_token
from ragz.modules.tenancy.models import RoleTemplate, UserGroup, WorkspaceMember
from ragz.modules.tenancy.permissions import DEFAULT_USER_PERMISSIONS, PERMISSIONS


@dataclass(frozen=True)
class TenantContext:
    user_id: UUID
    org_id: UUID
    role: str
    workspace_ids: frozenset[UUID]
    group_ids: frozenset[UUID] = frozenset()
    # Plan H (RBAC-2): appended LAST (H-C5 convention) so every existing
    # construction site -- including the isolation suites' replace(...) calls --
    # stays valid with the default.
    permissions: frozenset[str] = frozenset()


_bearer = HTTPBearer(auto_error=False)

_log = structlog.get_logger("ragz.tenancy")

# RBAC-05: permissions that are NEVER auto-granted, even to admin/superadmin --
# they must come from an explicit role-template overlay (e.g. the seeded
# "Content Manager" template). Task 10 EXTENDS this exact set; keep the name.
_AUTOMATIC_CARVE_OUTS = frozenset({"documents.acl.bypass"})


async def build_context_for_user(
    session: AsyncSession, user: User, *, workspace_ids: frozenset[UUID] | None = None
) -> TenantContext:
    """Turns a loaded `User` into a `TenantContext`. `workspace_ids`, when given,
    is used VERBATIM instead of the user's full membership set -- the
    key-narrowing hook that lets an API key scope a request to a single
    workspace (see `api_key_context` in `api/routes/external.py`)."""
    ws_ids = (
        workspace_ids
        if workspace_ids is not None
        else frozenset(
            (
                await session.execute(
                    select(WorkspaceMember.workspace_id).where(
                        WorkspaceMember.user_id == user.id
                    )
                )
            ).scalars().all()
        )
    )
    group_ids = frozenset(
        (
            await session.execute(
                select(UserGroup.group_id).where(UserGroup.user_id == user.id)
            )
        ).scalars().all()
    )
    if user.role in ("admin", "superadmin"):
        # RBAC-05: admin/superadmin hold every permission EXCEPT the carve-outs
        # (documents.acl.bypass), which are earned only via an explicit
        # role-template overlay. A dangling custom_role_id for this tier just
        # fails to grant the overlay (logged loudly) -- it does NOT deny-all
        # like the user-tier branch below, because the admin base floor
        # (PERMISSIONS minus the carve-outs) is already the least-destructive
        # floor for this tier.
        perms = PERMISSIONS - _AUTOMATIC_CARVE_OUTS
        if user.custom_role_id is not None:
            template = await session.get(RoleTemplate, user.custom_role_id)
            if template is not None:
                perms = perms | frozenset(template.permissions)
            else:
                _log.error(
                    "tenancy.dangling_custom_role_id",
                    user_id=str(user.id), custom_role_id=str(user.custom_role_id),
                )
    elif user.custom_role_id is not None:
        template = await session.get(RoleTemplate, user.custom_role_id)
        if template is None:
            # RBAC-04: a dangling role reference must fail CLOSED, never fall
            # back to a broad default -- that would let deleting/corrupting a
            # role SILENTLY increase access. The FK is ON DELETE SET NULL, so
            # reaching this branch means a corrupted row or an out-of-band
            # write; quarantine to zero permissions until an admin
            # re-assigns a real role, and surface it loudly.
            _log.error(
                "tenancy.dangling_custom_role_id",
                user_id=str(user.id), custom_role_id=str(user.custom_role_id),
            )
            perms = frozenset()
        else:
            perms = frozenset(template.permissions)
    else:
        perms = DEFAULT_USER_PERMISSIONS
    return TenantContext(
        user_id=user.id, org_id=user.org_id, role=user.role,
        workspace_ids=ws_ids, group_ids=group_ids, permissions=perms,
    )


async def build_verified_principal_context(
    session: AsyncSession, user: User, *, workspace_id: UUID
) -> TenantContext:
    """RBAC-02 (audit release blocker): a service credential (API key / bot)
    must be revalidated against CURRENT state on EVERY request, never trusting
    the workspace captured at issuance. Requires the backing user to still be a
    member of `workspace_id` AND to still hold `chat.generate` (the same action
    a human passes on the chat route -- the granular successor to the legacy
    chat.use flag, which the deny-by-default change retired from the default);
    denies non-enumerating otherwise. Unlike the raw `build_context_for_user`
    narrowing hook, this never injects a stored workspace the user is no longer
    entitled to."""
    member = (
        await session.execute(
            select(WorkspaceMember.workspace_id).where(
                WorkspaceMember.user_id == user.id,
                WorkspaceMember.workspace_id == workspace_id,
            )
        )
    ).scalar_one_or_none()
    if member is None:
        raise AuthenticationError("invalid or revoked credential")
    ctx = await build_context_for_user(session, user, workspace_ids=frozenset({workspace_id}))
    if "chat.generate" not in ctx.permissions and ctx.role != "superadmin":
        raise AuthenticationError("invalid or revoked credential")
    return ctx


async def get_tenant_context(
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TenantContext:
    if creds is None:
        raise AuthenticationError("missing bearer token")
    signing_key = await get_or_create_signing_key(session)
    claims = decode_access_token(creds.credentials, signing_key)
    result = await session.execute(select(User).where(User.id == claims.user_id))
    user = result.scalar_one_or_none()
    if user is None or not user.active:
        raise AuthenticationError("unknown or inactive user")
    return await build_context_for_user(session, user)


def require_role(*roles: str) -> Callable[..., Awaitable[TenantContext]]:
    async def guard(ctx: Annotated[TenantContext, Depends(get_tenant_context)]) -> TenantContext:
        if ctx.role != "superadmin" and ctx.role not in roles:
            raise AuthorizationError(f"requires role in {sorted(roles)}")
        return ctx

    return guard


def require_action(
    action: str, *, scope: str = "workspace"
) -> Callable[..., Awaitable[TenantContext]]:
    """Central authorization decision point (RBAC-06): every declared route
    action funnels through here. Superadmin bypass matches require_role;
    admins pass because get_tenant_context grants them every permission.
    Custom roles refine the 'user' tier only. `scope`
    (self|workspace|organization|platform) documents the action's resource
    scope for api/policy.py's route registry and the /me/authorization
    response (a later task) -- it does not itself widen or narrow the check
    below; resource-scope enforcement stays where it already lives
    (get_workspace_checked/get_document_checked/etc)."""

    async def guard(ctx: Annotated[TenantContext, Depends(get_tenant_context)]) -> TenantContext:
        if ctx.role != "superadmin" and action not in ctx.permissions:
            raise AuthorizationError(f"requires permission {action}")
        return ctx

    return guard


def rate_limit_user(
    scope: str, limit: int, window_seconds: int
) -> Callable[..., Awaitable[TenantContext]]:
    """Per-USER rate limit (chat endpoints, iron rule 4). Shares the single
    Redis fixed-window limiter (`check_rate_limit`) with `rate_limit()`
    (per-IP) -- one true rate-limiting code path for the whole app."""

    async def guard(
        request: Request,
        ctx: Annotated[TenantContext, Depends(get_tenant_context)],
    ) -> TenantContext:
        redis: Redis = request.app.state.redis
        await check_rate_limit(redis, f"rl:{scope}:user:{ctx.user_id}", limit, window_seconds)
        return ctx

    return guard
