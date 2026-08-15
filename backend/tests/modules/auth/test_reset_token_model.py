from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ragz.modules.auth.models import PasswordResetToken, User
from ragz.modules.auth.passwords import hash_password
from ragz.modules.tenancy.models import Organization


async def _make_user(session: AsyncSession, email: str = "reset@acme.com") -> User:
    org = Organization(name=f"org-{email}")
    session.add(org)
    await session.flush()
    user = User(org_id=org.id, email=email, password_hash=hash_password("pw123456"), role="user")
    session.add(user)
    await session.commit()
    return user


async def test_password_reset_token_round_trip(session: AsyncSession) -> None:
    user = await _make_user(session)
    expires_at = datetime.now(UTC).replace(tzinfo=None) + timedelta(minutes=45)
    token = PasswordResetToken(user_id=user.id, token_hash="h" * 64, expires_at=expires_at)
    session.add(token)
    await session.commit()

    row = (
        await session.execute(
            select(PasswordResetToken).where(PasswordResetToken.user_id == user.id)
        )
    ).scalar_one()
    assert row.token_hash == "h" * 64
    assert row.expires_at == expires_at
    assert row.used_at is None
    assert row.created_at is not None
    assert row.id is not None


async def test_password_reset_token_hash_unique(session: AsyncSession) -> None:
    user = await _make_user(session)
    expires_at = datetime.now(UTC).replace(tzinfo=None) + timedelta(minutes=45)
    session.add(
        PasswordResetToken(user_id=user.id, token_hash="dupe" * 16, expires_at=expires_at)
    )
    await session.commit()

    session.add(
        PasswordResetToken(user_id=user.id, token_hash="dupe" * 16, expires_at=expires_at)
    )
    with pytest.raises(IntegrityError):
        await session.commit()


async def test_password_reset_token_cascades_on_user_delete(session: AsyncSession) -> None:
    user = await _make_user(session, email="cascade@acme.com")
    expires_at = datetime.now(UTC).replace(tzinfo=None) + timedelta(minutes=45)
    token = PasswordResetToken(user_id=user.id, token_hash=str(uuid4()), expires_at=expires_at)
    session.add(token)
    await session.commit()
    token_id = token.id

    await session.delete(user)
    await session.commit()

    row = (
        await session.execute(
            select(PasswordResetToken).where(PasswordResetToken.id == token_id)
        )
    ).scalar_one_or_none()
    assert row is None
