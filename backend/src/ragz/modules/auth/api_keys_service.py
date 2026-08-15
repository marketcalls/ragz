"""Superadmin-controlled API keys for the external API (iron rule 3). The raw
key is returned exactly once by generate_api_key; only a lookup prefix + a
peppered SHA-256 hash are stored. resolve_api_key is the single verification
path (prefix lookup -> constant-work hash compare -> revoked/expired gate).

sec RAGZ-PUB-13: no key may be perpetual. generate_api_key bounds every
created key to settings.api_key_max_lifetime_days -- a caller who supplies no
expires_at gets one defaulted to now + max lifetime; a caller who supplies
one further out than that gets it CAPPED to the same ceiling (silently, not
rejected -- friendlier for callers who over-ask, and still fully closes the
"perpetual key" gap). resolve_api_key enforces expiry at auth time, and
additionally treats a legacy NULL expires_at (rows written before this fix)
as expiring at created_at + max lifetime rather than never -- so pre-fix keys
age out too instead of staying perpetually valid."""

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ragz.core.config import Settings
from ragz.core.db import naive_utc
from ragz.core.errors import ConflictError, NotFoundError
from ragz.modules.auth.models import ApiKey
from ragz.modules.auth.service import _hash
from ragz.modules.tenancy.models import Workspace, WorkspaceMember

RAW_PREFIX = "ragz_sk_"
KEY_PREFIX_LEN = 12


@dataclass(frozen=True)
class ApiKeyPrincipal:
    key_id: UUID
    org_id: UUID
    user_id: UUID
    workspace_id: UUID


async def generate_api_key(
    session: AsyncSession, settings: Settings, *, actor_id: UUID, name: str,
    user_id: UUID, workspace_id: UUID, expires_at: datetime | None,
) -> tuple[ApiKey, str]:
    ws = (
        await session.execute(select(Workspace).where(Workspace.id == workspace_id))
    ).scalar_one_or_none()
    if ws is None:
        raise ConflictError("workspace not found")
    member = (
        await session.execute(
            select(WorkspaceMember.user_id).where(
                WorkspaceMember.workspace_id == workspace_id,
                WorkspaceMember.user_id == user_id,
            )
        )
    ).scalar_one_or_none()
    if member is None:
        raise ConflictError("user is not a member of that workspace")
    raw = RAW_PREFIX + secrets.token_urlsafe(32)
    row = ApiKey(
        prefix=raw[:KEY_PREFIX_LEN],
        key_hash=_hash(raw, settings.api_key_pepper),
        name=name, org_id=ws.org_id, user_id=user_id, workspace_id=workspace_id,
        created_by=actor_id,
        expires_at=_bound_expiry(expires_at, settings.api_key_max_lifetime_days),
    )
    session.add(row)
    await session.commit()
    return row, raw


async def list_api_keys(session: AsyncSession) -> list[ApiKey]:
    return list(
        (await session.execute(select(ApiKey).order_by(ApiKey.created_at.desc()))).scalars()
    )


async def get_api_key(session: AsyncSession, *, key_id: UUID) -> ApiKey:
    """Direct lookup for the revoke path (RBAC-07): the audit event must be
    attributed to the KEY's own org, and a missing key must 404 rather than
    silently succeed. Raises NotFoundError when absent."""
    row = (
        await session.execute(select(ApiKey).where(ApiKey.id == key_id))
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError("api key not found")
    return row


async def revoke_api_key(session: AsyncSession, *, key_id: UUID) -> None:
    await session.execute(
        update(ApiKey).where(ApiKey.id == key_id).values(revoked_at=naive_utc())
    )
    await session.commit()


async def resolve_api_key(
    session: AsyncSession, settings: Settings, *, raw_key: str
) -> ApiKeyPrincipal | None:
    if not raw_key.startswith(RAW_PREFIX):
        return None
    row = (
        await session.execute(
            select(ApiKey).where(ApiKey.prefix == raw_key[:KEY_PREFIX_LEN])
        )
    ).scalar_one_or_none()
    if row is None:
        return None
    if not secrets.compare_digest(row.key_hash, _hash(raw_key, settings.api_key_pepper)):
        return None
    if row.revoked_at is not None:
        return None
    now = datetime.now(UTC)
    # sec RAGZ-PUB-13: bound rows always have an expires_at (see
    # generate_api_key/_bound_expiry) and are rejected once it passes. Legacy
    # rows written before this fix can still carry a NULL expires_at -- treat
    # those as expiring at created_at + max lifetime rather than never, so
    # pre-fix keys age out instead of staying perpetually valid.
    effective_expiry = row.expires_at
    if effective_expiry is None:
        effective_expiry = row.created_at + timedelta(days=settings.api_key_max_lifetime_days)
    if effective_expiry.replace(tzinfo=UTC) < now:
        return None
    row.last_used_at = naive_utc()
    await session.commit()
    return ApiKeyPrincipal(
        key_id=row.id, org_id=row.org_id, user_id=row.user_id, workspace_id=row.workspace_id
    )


def _bound_expiry(expires_at: datetime | None, max_lifetime_days: int) -> datetime:
    """sec RAGZ-PUB-13: no key may be created non-expiring. No caller-supplied
    expiry -> default to now + max lifetime. A caller-supplied expiry further
    out than that ceiling -> capped to it (not rejected: friendlier, and
    equally closes the perpetual-key gap). Always returns a naive-UTC
    datetime -- never None."""
    ceiling = datetime.now(UTC) + timedelta(days=max_lifetime_days)
    if expires_at is None:
        bounded = ceiling
    else:
        requested = expires_at if expires_at.tzinfo else expires_at.replace(tzinfo=UTC)
        bounded = min(requested, ceiling)
    return bounded.astimezone(UTC).replace(tzinfo=None)
