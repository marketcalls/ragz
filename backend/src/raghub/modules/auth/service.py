import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from raghub.core.app_settings import get_or_create_signing_key
from raghub.core.config import Settings
from raghub.core.errors import AuthenticationError
from raghub.modules.auth.models import RefreshToken, User
from raghub.modules.auth.passwords import verify_password
from raghub.modules.auth.tokens import issue_access_token


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
        raise AuthenticationError("invalid credentials")
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
        raise AuthenticationError("unknown refresh token")
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
        raise AuthenticationError("refresh token reuse detected")
    if row.expires_at.replace(tzinfo=UTC) < now:
        raise AuthenticationError("refresh token expired")
    user = (await session.execute(select(User).where(User.id == row.user_id))).scalar_one()
    if not user.active:
        # Token stays untouched: an inactive user can't rotate anyway, and if
        # reactivated the token resumes working within its original expiry.
        raise AuthenticationError("user inactive")
    row.revoked_at = now_naive
    return await _issue_pair(session, user, row.family_id, settings)


async def logout(session: AsyncSession, *, raw_refresh: str) -> None:
    await session.execute(
        update(RefreshToken)
        .where(RefreshToken.token_hash == _hash(raw_refresh))
        .values(revoked_at=datetime.now(UTC).replace(tzinfo=None))
    )
    await session.commit()
