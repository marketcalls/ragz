"""External API surface (Task 4 adds the actual chat route here). This module
owns the API-key auth dependency: it resolves a raw key via the single
`resolve_api_key` verification path (iron rule 3), loads+checks the owning
user, and narrows the resulting `TenantContext` to the key's single
workspace_id (the key-narrowing hook in `build_context_for_user`)."""

from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ragz.api.deps import get_session
from ragz.core.config import Settings, get_settings
from ragz.core.errors import AuthenticationError
from ragz.modules.auth.api_keys_service import resolve_api_key
from ragz.modules.auth.models import User
from ragz.modules.tenancy.context import TenantContext, build_context_for_user

SessionDep = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


def _extract_key(request: Request) -> str | None:
    auth = request.headers.get("authorization")
    if auth and auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.headers.get("x-api-key")


async def api_key_context(
    request: Request, session: SessionDep, settings: SettingsDep
) -> TenantContext:
    raw = _extract_key(request)
    if not raw:
        raise AuthenticationError("missing API key")
    principal = await resolve_api_key(session, settings, raw_key=raw)
    if principal is None:
        raise AuthenticationError("invalid API key")
    user = (
        await session.execute(select(User).where(User.id == principal.user_id))
    ).scalar_one_or_none()
    if user is None or not user.active:
        raise AuthenticationError("invalid API key")
    return await build_context_for_user(
        session, user, workspace_ids=frozenset({principal.workspace_id})
    )


ApiKeyDep = Annotated[TenantContext, Depends(api_key_context)]
