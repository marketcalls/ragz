import hashlib
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ragz.core.app_settings import get_or_create_signing_key
from ragz.core.config import Settings
from ragz.core.errors import AuthenticationError
from ragz.modules.auth.models import RefreshToken, User
from ragz.modules.auth.passwords import hash_password
from ragz.modules.auth.service import login, login_oidc, logout, rotate_refresh
from ragz.modules.auth.tokens import decode_access_token
from ragz.modules.tenancy.models import Organization

SETTINGS = Settings(_env_file=None)
# Grace disabled: reuse of a rotated token is an instant theft signal.
NO_GRACE = Settings(_env_file=None, refresh_reuse_grace_seconds=0)


async def _token_row(session: AsyncSession, raw_refresh: str) -> RefreshToken:
    token_hash = hashlib.sha256(raw_refresh.encode()).hexdigest()
    return (
        await session.execute(select(RefreshToken).where(RefreshToken.token_hash == token_hash))
    ).scalar_one()


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
    pair2 = await rotate_refresh(session, raw_refresh=pair1.refresh_token, settings=NO_GRACE)
    assert pair2.refresh_token != pair1.refresh_token
    # reusing the rotated (old) token is an attack signal -> whole family dies
    with pytest.raises(AuthenticationError):
        await rotate_refresh(session, raw_refresh=pair1.refresh_token, settings=NO_GRACE)
    with pytest.raises(AuthenticationError):
        await rotate_refresh(session, raw_refresh=pair2.refresh_token, settings=NO_GRACE)


async def test_concurrent_tab_reuse_within_grace_reissues_same_family(
    session: AsyncSession,
) -> None:
    """Two tabs racing on the same refresh cookie: the loser presents the
    just-rotated token within the grace window and must get a fresh pair
    chained to the SAME family instead of tripping theft detection.

    Pinned behavior: BOTH descendants stay live — the winner's token (T2) and
    the grace-issued token (T3) each rotate normally afterwards."""
    await make_user(session)
    pair1 = await login(
        session, email="u@acme.com", password="pw123456", settings=SETTINGS  # noqa: S106
    )
    pair2 = await rotate_refresh(session, raw_refresh=pair1.refresh_token, settings=SETTINGS)
    # the losing tab replays T microseconds after the winner rotated it
    pair3 = await rotate_refresh(session, raw_refresh=pair1.refresh_token, settings=SETTINGS)
    assert pair3.refresh_token not in {pair1.refresh_token, pair2.refresh_token}
    rows = (await session.execute(select(RefreshToken))).scalars().all()
    assert len({r.family_id for r in rows}) == 1  # everything chained to one family
    # family NOT revoked: both live tokens still rotate
    pair4 = await rotate_refresh(session, raw_refresh=pair2.refresh_token, settings=SETTINGS)
    pair5 = await rotate_refresh(session, raw_refresh=pair3.refresh_token, settings=SETTINGS)
    assert pair4.refresh_token and pair5.refresh_token


async def test_reuse_after_grace_window_revokes_family(session: AsyncSession) -> None:
    await make_user(session)
    pair1 = await login(
        session, email="u@acme.com", password="pw123456", settings=SETTINGS  # noqa: S106
    )
    pair2 = await rotate_refresh(session, raw_refresh=pair1.refresh_token, settings=SETTINGS)
    # push the original rotation outside the grace window
    row1 = await _token_row(session, pair1.refresh_token)
    assert row1.revoked_at is not None
    row1.revoked_at -= timedelta(seconds=SETTINGS.refresh_reuse_grace_seconds + 5)
    await session.commit()
    with pytest.raises(AuthenticationError, match="invalid refresh token"):
        await rotate_refresh(session, raw_refresh=pair1.refresh_token, settings=SETTINGS)
    # theft response unchanged: the whole family is dead, current token included
    with pytest.raises(AuthenticationError, match="invalid refresh token"):
        await rotate_refresh(session, raw_refresh=pair2.refresh_token, settings=SETTINGS)


async def test_expired_revoked_token_inside_grace_still_rejected(
    session: AsyncSession,
) -> None:
    """Expiry wins over the grace window: a revoked-and-expired token never
    reissues, even when presented inside the grace window."""
    await make_user(session)
    pair1 = await login(
        session, email="u@acme.com", password="pw123456", settings=SETTINGS  # noqa: S106
    )
    pair2 = await rotate_refresh(session, raw_refresh=pair1.refresh_token, settings=SETTINGS)
    row1 = await _token_row(session, pair1.refresh_token)
    row1.expires_at = (datetime.now(UTC) - timedelta(seconds=60)).replace(tzinfo=None)
    await session.commit()
    with pytest.raises(AuthenticationError, match="invalid refresh token"):
        await rotate_refresh(session, raw_refresh=pair1.refresh_token, settings=SETTINGS)
    # benign rejection (not a theft signal): the winner's session survives
    pair3 = await rotate_refresh(session, raw_refresh=pair2.refresh_token, settings=SETTINGS)
    assert pair3.refresh_token


async def test_rotate_refresh_after_security_version_bump_reissues_current_sv(
    session: AsyncSession,
) -> None:
    """sec RAGZ-PUB-06: rotate_refresh re-loads the user row on every call, so
    a refresh presented AFTER security_version was bumped mints a NEW access
    token stamped with the CURRENT value -- re-syncing that session instead
    of being permanently locked out like a stale access token is. Bumps the
    column directly (not via change_password/reset_password, which also
    revoke every refresh-token family) to isolate this exact code path."""
    user = await make_user(session)
    pair1 = await login(
        session, email="u@acme.com", password="pw123456", settings=SETTINGS  # noqa: S106
    )
    signing_key = await get_or_create_signing_key(session, SETTINGS)
    old_claims = decode_access_token(pair1.access_token, signing_key)
    assert old_claims.sv == 0

    user.security_version = 1
    session.add(user)
    await session.commit()

    pair2 = await rotate_refresh(session, raw_refresh=pair1.refresh_token, settings=SETTINGS)
    new_claims = decode_access_token(pair2.access_token, signing_key)
    assert new_claims.sv == 1


async def test_logout_revoked_token_gets_no_grace(session: AsyncSession) -> None:
    """A token revoked by logout has no rotation successor; replaying it within
    seconds of logout must NOT resurrect the session via the grace window."""
    await make_user(session)
    pair = await login(
        session, email="u@acme.com", password="pw123456", settings=SETTINGS  # noqa: S106
    )
    await logout(session, raw_refresh=pair.refresh_token, settings=SETTINGS)
    with pytest.raises(AuthenticationError, match="invalid refresh token"):
        await rotate_refresh(session, raw_refresh=pair.refresh_token, settings=SETTINGS)


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
        await login_oidc(
            session, email="new.hire@acme.com",
            issuer="https://idp.example.com", subject="idp-sub-1", settings=SETTINGS,
        )

    # no user was provisioned into either org as a side effect of the failed attempt
    rows = (
        await session.execute(select(User).where(User.email == "new.hire@acme.com"))
    ).scalars().all()
    assert rows == []


async def test_login_oidc_new_email_under_allowed_domain_jit_creates_and_binds(
    session: AsyncSession,
) -> None:
    """The accepted enterprise path: a brand-new email under an org's SSO
    allowlist gets JIT-provisioned and bound to (issuer, subject) in one
    step -- unchanged by the RAGZ-PUB-02 auto-link fix."""
    org = Organization(name="Acme", sso_domains=["acme.com"])
    session.add(org)
    await session.commit()

    pair = await login_oidc(
        session, email="brand.new@acme.com",
        issuer="https://idp.example.com", subject="idp-sub-1", settings=SETTINGS,
    )
    assert pair.access_token and pair.refresh_token

    user = (
        await session.execute(select(User).where(User.email == "brand.new@acme.com"))
    ).scalar_one()
    assert user.org_id == org.id
    assert user.oidc_issuer == "https://idp.example.com"
    assert user.oidc_subject == "idp-sub-1"


async def test_login_oidc_already_bound_identity_logs_in(session: AsyncSession) -> None:
    """(issuer, subject) already bound to a user: that user logs straight in
    on a repeat visit, with no further mutation of the binding."""
    org = Organization(name="Acme", sso_domains=["acme.com"])
    session.add(org)
    await session.commit()
    bound_user = User(
        org_id=org.id, email="returning@acme.com",
        password_hash=hash_password("irrelevant-pw1"), role="user",  # noqa: S106
        oidc_issuer="https://idp.example.com", oidc_subject="idp-sub-1",
    )
    session.add(bound_user)
    await session.commit()

    pair = await login_oidc(
        session, email="returning@acme.com",
        issuer="https://idp.example.com", subject="idp-sub-1", settings=SETTINGS,
    )
    assert pair.access_token and pair.refresh_token

    await session.refresh(bound_user)
    assert bound_user.oidc_issuer == "https://idp.example.com"
    assert bound_user.oidc_subject == "idp-sub-1"


async def test_login_oidc_rejects_unbound_existing_user_same_email(
    session: AsyncSession,
) -> None:
    """sec RAGZ-PUB-02 (residual gap fix): an existing user with NO
    (issuer, subject) binding yet -- e.g. a password account -- must NOT be
    auto-linked/logged-in just because the asserted email matches, even
    though the domain is claimed by the account's own org via the SSO
    allowlist. Auto-linking here would be an unauthenticated email-based
    account takeover. No bind, no session; the old auto-link behavior this
    replaces is gone."""
    org = Organization(name="Acme", sso_domains=["acme.com"])
    session.add(org)
    await session.commit()
    local_user = User(
        org_id=org.id, email="local@acme.com",
        password_hash=hash_password("irrelevant-pw1"), role="user",  # noqa: S106
    )
    session.add(local_user)
    await session.commit()
    assert local_user.oidc_issuer is None and local_user.oidc_subject is None

    with pytest.raises(AuthenticationError):
        await login_oidc(
            session, email="local@acme.com",
            issuer="https://idp.example.com", subject="idp-sub-new", settings=SETTINGS,
        )

    await session.refresh(local_user)
    assert local_user.oidc_issuer is None
    assert local_user.oidc_subject is None
    # no rogue refresh session was minted for this user as a side effect
    rows = (
        await session.execute(select(RefreshToken).where(RefreshToken.user_id == local_user.id))
    ).scalars().all()
    assert rows == []


async def test_login_oidc_rejects_mismatched_existing_binding(session: AsyncSession) -> None:
    """An existing user already bound to a DIFFERENT (issuer, subject) must
    be rejected outright -- unchanged behavior, re-asserted at the service
    layer alongside the new unbound-existing-user rejection above."""
    org = Organization(name="Acme", sso_domains=["acme.com"])
    session.add(org)
    await session.commit()
    bound_user = User(
        org_id=org.id, email="bound@acme.com",
        password_hash=hash_password("irrelevant-pw1"), role="user",  # noqa: S106
        oidc_issuer="https://idp.example.com", oidc_subject="idp-sub-original",
    )
    session.add(bound_user)
    await session.commit()

    with pytest.raises(AuthenticationError):
        await login_oidc(
            session, email="bound@acme.com",
            issuer="https://idp.example.com", subject="idp-sub-different", settings=SETTINGS,
        )

    await session.refresh(bound_user)
    assert bound_user.oidc_subject == "idp-sub-original"


async def test_logout_revokes(session: AsyncSession) -> None:
    await make_user(session)
    pair = await login(
        session, email="u@acme.com", password="pw123456", settings=SETTINGS  # noqa: S106
    )
    await logout(session, raw_refresh=pair.refresh_token, settings=SETTINGS)
    with pytest.raises(AuthenticationError):
        await rotate_refresh(session, raw_refresh=pair.refresh_token, settings=SETTINGS)
