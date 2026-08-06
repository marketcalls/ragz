"""Superadmin-controlled API keys for the external API (iron rule 3). The raw
key is returned exactly once by generate_api_key; only a lookup prefix + a
peppered SHA-256 hash are stored. resolve_api_key is the single verification
path (prefix lookup -> constant-work hash compare -> revoked/expired gate)."""

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ragz.core.config import Settings
from ragz.core.db import naive_utc
from ragz.core.errors import ConflictError
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
        created_by=actor_id, expires_at=_naive(expires_at),
    )
    session.add(row)
    await session.commit()
    return row, raw


async def list_api_keys(session: AsyncSession) -> list[ApiKey]:
    return list(
        (await session.execute(select(ApiKey).order_by(ApiKey.created_at.desc()))).scalars()
    )


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
    if row.expires_at is not None and row.expires_at.replace(tzinfo=UTC) < datetime.now(UTC):
        return None
    row.last_used_at = naive_utc()
    await session.commit()
    return ApiKeyPrincipal(
        key_id=row.id, org_id=row.org_id, user_id=row.user_id, workspace_id=row.workspace_id
    )


def _naive(dt: datetime | None) -> datetime | None:
    return None if dt is None else dt.astimezone(UTC).replace(tzinfo=None)
