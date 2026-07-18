import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import structlog
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from raghub.core.app_settings import get_or_create_signing_key
from raghub.core.config import Settings
from raghub.core.db import naive_utc
from raghub.core.errors import AuthenticationError, ConflictError, NotFoundError
from raghub.modules.audit.service import record_audit
from raghub.modules.auth.models import Invitation, RefreshToken, User
from raghub.modules.auth.passwords import hash_password, verify_password
from raghub.modules.auth.tokens import issue_access_token
from raghub.modules.tenancy.context import TenantContext


@dataclass(frozen=True)
class TokenPair:
    access_token: str
    refresh_token: str


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


async def _issue_pair(
    session: AsyncSession, user: User, family_id: UUID, settings: Settings
) -> TokenPair:
    signing_key = await get_or_create_signing_key(session)
    raw_refresh = secrets.token_urlsafe(48)
    ttl = timedelta(seconds=settings.refresh_token_ttl_seconds)
    expires_at = (datetime.now(UTC) + ttl).replace(tzinfo=None)
    session.add(
        RefreshToken(
            user_id=user.id,
            family_id=family_id,
            token_hash=_hash(raw_refresh),
            expires_at=expires_at,
        )
    )
    await session.commit()
    access = issue_access_token(
        user_id=user.id, org_id=user.org_id, role=user.role,
        signing_key=signing_key, ttl_seconds=settings.access_token_ttl_seconds,
    )
    return TokenPair(access_token=access, refresh_token=raw_refresh)


async def login(
    session: AsyncSession, *, email: str, password: str, settings: Settings
) -> TokenPair:
    user = (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if user is None or not user.active or not verify_password(user.password_hash, password):
        await record_audit(session, org_id=None, actor_id=None, action="login.failure",
                           target_type="user", target_id=email)
        await session.commit()
        raise AuthenticationError("invalid credentials")
    await record_audit(session, org_id=user.org_id, actor_id=user.id, action="login.success",
                       target_type="user", target_id=str(user.id))
    return await _issue_pair(session, user, uuid4(), settings)


async def rotate_refresh(
    session: AsyncSession, *, raw_refresh: str, settings: Settings
) -> TokenPair:
    row = (
        await session.execute(
            select(RefreshToken)
            .where(RefreshToken.token_hash == _hash(raw_refresh))
            .with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        structlog.get_logger().info("refresh_rejected", reason="unknown")
        raise AuthenticationError("invalid refresh token")
    now = datetime.now(UTC)
    now_naive = now.replace(tzinfo=None)
    if row.revoked_at is not None:
        # Reuse of a rotated token: revoke the entire family.
        await session.execute(
            update(RefreshToken)
            .where(RefreshToken.family_id == row.family_id)
            .values(revoked_at=now_naive)
        )
        await session.commit()
        structlog.get_logger().info("refresh_rejected", reason="reuse_detected")
        raise AuthenticationError("invalid refresh token")
    if row.expires_at.replace(tzinfo=UTC) < now:
        structlog.get_logger().info("refresh_rejected", reason="expired")
        raise AuthenticationError("invalid refresh token")
    user = (await session.execute(select(User).where(User.id == row.user_id))).scalar_one()
    if not user.active:
        # Token stays untouched: an inactive user can't rotate anyway, and if
        # reactivated the token resumes working within its original expiry.
        structlog.get_logger().info("refresh_rejected", reason="user_inactive")
        raise AuthenticationError("invalid refresh token")
    row.revoked_at = now_naive
    return await _issue_pair(session, user, row.family_id, settings)


async def logout(session: AsyncSession, *, raw_refresh: str) -> None:
    await session.execute(
        update(RefreshToken)
        .where(RefreshToken.token_hash == _hash(raw_refresh))
        .values(revoked_at=naive_utc())
    )
    await session.commit()


async def create_invitation(
    session: AsyncSession, ctx: TenantContext, *, email: str, role: str, ttl_hours: int = 72
) -> str:
    existing = (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if existing is not None:
        raise ConflictError("email already registered")
    raw = secrets.token_urlsafe(32)
    expires_at = (datetime.now(UTC) + timedelta(hours=ttl_hours)).replace(tzinfo=None)
    invitation = Invitation(
        org_id=ctx.org_id, email=email, role=role, token_hash=_hash(raw),
        expires_at=expires_at,
    )
    session.add(invitation)
    await session.flush()
    await record_audit(session, org_id=ctx.org_id, actor_id=ctx.user_id,
                       action="invitation.created", target_type="invitation",
                       target_id=str(invitation.id))
    await session.commit()
    return raw


async def accept_invitation(session: AsyncSession, *, raw_token: str, password: str) -> User:
    inv = (
        await session.execute(select(Invitation).where(Invitation.token_hash == _hash(raw_token)))
    ).scalar_one_or_none()
    now = datetime.now(UTC)
    if inv is None or inv.accepted_at is not None or inv.expires_at.replace(tzinfo=UTC) < now:
        raise AuthenticationError("invalid or expired invitation")
    inv.accepted_at = now.replace(tzinfo=None)
    user = User(org_id=inv.org_id, email=inv.email,
                password_hash=hash_password(password), role=inv.role)
    session.add(user)
    await session.flush()
    await record_audit(session, org_id=inv.org_id, actor_id=None,
                       action="invitation.accepted", target_type="user",
                       target_id=str(user.id))
    await session.commit()
    return user


async def list_users(session: AsyncSession, ctx: TenantContext) -> list[User]:
    return list(
        (
            await session.execute(
                select(User)
                .where(User.org_id == ctx.org_id, User.role != "superadmin")
                .order_by(User.email)
            )
        ).scalars()
    )


async def _org_user(session: AsyncSession, ctx: TenantContext, user_id: UUID) -> User:
    user = (
        await session.execute(
            select(User).where(User.id == user_id, User.org_id == ctx.org_id)
        )
    ).scalar_one_or_none()
    if user is None or user.role == "superadmin":
        raise NotFoundError("user not found")
    return user


async def get_org_user(session: AsyncSession, ctx: TenantContext, user_id: UUID) -> User:
    return await _org_user(session, ctx, user_id)


async def set_user_active(
    session: AsyncSession, ctx: TenantContext, user_id: UUID, active: bool
) -> User:
    user = await _org_user(session, ctx, user_id)
    user.active = active
    if not active:
        await record_audit(session, org_id=ctx.org_id, actor_id=ctx.user_id,
                           action="user.deactivated", target_type="user",
                           target_id=str(user_id))
    await session.commit()
    return user


async def set_user_role(
    session: AsyncSession, ctx: TenantContext, user_id: UUID, role: str
) -> User:
    user = await _org_user(session, ctx, user_id)
    user.role = role
    await record_audit(session, org_id=ctx.org_id, actor_id=ctx.user_id,
                       action="user.role_changed", target_type="user",
                       target_id=str(user_id))
    await session.commit()
    return user
