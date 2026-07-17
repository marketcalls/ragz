from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from raghub.api.deps import get_session
from raghub.core.app_settings import get_or_create_signing_key
from raghub.core.errors import AuthenticationError, AuthorizationError
from raghub.modules.auth.models import User
from raghub.modules.auth.tokens import decode_access_token
from raghub.modules.tenancy.models import WorkspaceMember


@dataclass(frozen=True)
class TenantContext:
    user_id: UUID
    org_id: UUID
    role: str
    workspace_ids: frozenset[UUID]


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
    return TenantContext(
        user_id=user.id, org_id=user.org_id, role=user.role, workspace_ids=frozenset(ws_ids)
    )


def require_role(*roles: str) -> Callable[..., Awaitable[TenantContext]]:
    async def guard(ctx: Annotated[TenantContext, Depends(get_tenant_context)]) -> TenantContext:
        if ctx.role != "superadmin" and ctx.role not in roles:
            raise AuthorizationError(f"requires role in {sorted(roles)}")
        return ctx

    return guard
