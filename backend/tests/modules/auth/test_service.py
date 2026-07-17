import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from raghub.core.config import Settings
from raghub.core.errors import AuthenticationError
from raghub.modules.auth.models import User
from raghub.modules.auth.passwords import hash_password
from raghub.modules.auth.service import login, logout, rotate_refresh
from raghub.modules.tenancy.models import Organization

SETTINGS = Settings(_env_file=None)


async def make_user(session: AsyncSession, email: str = "u@acme.com") -> User:
    org = Organization(name=f"org-{email}")
    session.add(org)
    await session.flush()
    user = User(org_id=org.id, email=email, password_hash=hash_password("pw123456"), role="user")
    session.add(user)
    await session.commit()
    return user


async def test_login_returns_pair(session: AsyncSession) -> None:
    await make_user(session)
    pair = await login(
        session, email="u@acme.com", password="pw123456", settings=SETTINGS  # noqa: S106
    )
    assert pair.access_token and pair.refresh_token


async def test_login_wrong_password(session: AsyncSession) -> None:
    await make_user(session)
    with pytest.raises(AuthenticationError):
        await login(session, email="u@acme.com", password="nope", settings=SETTINGS)  # noqa: S106


async def test_rotation_and_reuse_revokes_family(session: AsyncSession) -> None:
    await make_user(session)
    pair1 = await login(
        session, email="u@acme.com", password="pw123456", settings=SETTINGS  # noqa: S106
    )
    pair2 = await rotate_refresh(session, raw_refresh=pair1.refresh_token, settings=SETTINGS)
    assert pair2.refresh_token != pair1.refresh_token
    # reusing the rotated (old) token is an attack signal -> whole family dies
    with pytest.raises(AuthenticationError):
        await rotate_refresh(session, raw_refresh=pair1.refresh_token, settings=SETTINGS)
    with pytest.raises(AuthenticationError):
        await rotate_refresh(session, raw_refresh=pair2.refresh_token, settings=SETTINGS)


async def test_logout_revokes(session: AsyncSession) -> None:
    await make_user(session)
    pair = await login(
        session, email="u@acme.com", password="pw123456", settings=SETTINGS  # noqa: S106
    )
    await logout(session, raw_refresh=pair.refresh_token)
    with pytest.raises(AuthenticationError):
        await rotate_refresh(session, raw_refresh=pair.refresh_token, settings=SETTINGS)
