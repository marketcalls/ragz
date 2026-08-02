from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

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
    ws_ids = (
        await session.execute(
            select(WorkspaceMember.workspace_id).where(WorkspaceMember.user_id == user.id)
        )
    ).scalars().all()
    group_ids = (
        await session.execute(
            select(UserGroup.group_id).where(UserGroup.user_id == user.id)
        )
    ).scalars().all()
    if user.role in ("admin", "superadmin"):
        perms = PERMISSIONS
    elif user.custom_role_id is not None:
        template = await session.get(RoleTemplate, user.custom_role_id)
        perms = frozenset(template.permissions) if template else DEFAULT_USER_PERMISSIONS
    else:
        perms = DEFAULT_USER_PERMISSIONS
    return TenantContext(
        user_id=user.id, org_id=user.org_id, role=user.role,
        workspace_ids=frozenset(ws_ids), group_ids=frozenset(group_ids), permissions=perms,
    )


def require_role(*roles: str) -> Callable[..., Awaitable[TenantContext]]:
    async def guard(ctx: Annotated[TenantContext, Depends(get_tenant_context)]) -> TenantContext:
        if ctx.role != "superadmin" and ctx.role not in roles:
            raise AuthorizationError(f"requires role in {sorted(roles)}")
        return ctx

    return guard


def require_permission(permission: str) -> Callable[..., Awaitable[TenantContext]]:
    """Granular guard (RBAC-2). Superadmin bypass matches require_role; admins
    pass because get_tenant_context grants them every permission. Custom roles
    refine the 'user' tier only."""

    async def guard(ctx: Annotated[TenantContext, Depends(get_tenant_context)]) -> TenantContext:
        if ctx.role != "superadmin" and permission not in ctx.permissions:
            raise AuthorizationError(f"requires permission {permission}")
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
