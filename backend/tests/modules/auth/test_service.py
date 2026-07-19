import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from raghub.core.config import Settings
from raghub.core.errors import AuthenticationError
from raghub.modules.auth.models import RefreshToken, User
from raghub.modules.auth.passwords import hash_password
from raghub.modules.auth.service import login, login_oidc, logout, rotate_refresh
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


async def test_rotate_refresh_inactive_user_leaves_token_untouched(
    session: AsyncSession,
) -> None:
    user = await make_user(session)
    pair = await login(
        session, email="u@acme.com", password="pw123456", settings=SETTINGS  # noqa: S106
    )
    user.active = False
    await session.commit()
    with pytest.raises(AuthenticationError):
        await rotate_refresh(session, raw_refresh=pair.refresh_token, settings=SETTINGS)
    row = (
        await session.execute(select(RefreshToken).where(RefreshToken.user_id == user.id))
    ).scalar_one()
    assert row.revoked_at is None


async def test_login_oidc_denies_ambiguous_domain_claim(session: AsyncSession) -> None:
    """Defense in depth: `set_org_sso_domains` now rejects a second org claiming
    an already-claimed domain (see tests/modules/tenancy/test_sso_domains.py),
    but `login_oidc` must fail loudly too if the data ever ends up ambiguous
    anyway (e.g. rows written before that guard existed, or direct DB edits) --
    it must never silently pick whichever org `.first()` happens to sort."""
    org_a = Organization(name="Acme-A", sso_domains=["acme.com"])
    org_b = Organization(name="Acme-B", sso_domains=["acme.com"])
    session.add_all([org_a, org_b])
    await session.flush()
    await session.commit()

    with pytest.raises(AuthenticationError):
        await login_oidc(session, email="new.hire@acme.com", settings=SETTINGS)

    # no user was provisioned into either org as a side effect of the failed attempt
    rows = (
        await session.execute(select(User).where(User.email == "new.hire@acme.com"))
    ).scalars().all()
    assert rows == []


async def test_logout_revokes(session: AsyncSession) -> None:
    await make_user(session)
    pair = await login(
        session, email="u@acme.com", password="pw123456", settings=SETTINGS  # noqa: S106
    )
    await logout(session, raw_refresh=pair.refresh_token)
    with pytest.raises(AuthenticationError):
        await rotate_refresh(session, raw_refresh=pair.refresh_token, settings=SETTINGS)
