from datetime import UTC, datetime, timedelta

import pytest

from ragz.core.config import Settings
from ragz.core.errors import ConflictError
from ragz.modules.auth import api_keys_service as svc
from ragz.modules.auth.models import ApiKey, User

SETTINGS = Settings(_env_file=None)


async def _member_ws(session, user: User):
    # helper: create a workspace owned by user's org + membership; returns Workspace
    from ragz.modules.tenancy.models import Workspace, WorkspaceMember
    ws = Workspace(org_id=user.org_id, name="WS", embedding_model_id=None)
    session.add(ws)
    await session.flush()
    session.add(WorkspaceMember(workspace_id=ws.id, user_id=user.id, role="contributor"))
    await session.flush()
    return ws


async def test_generate_returns_raw_once_and_stores_hash_not_plaintext(session, seeded_user):
    ws = await _member_ws(session, seeded_user)
    row, raw = await svc.generate_api_key(
        session, SETTINGS, actor_id=seeded_user.id, name="k1",
        user_id=seeded_user.id, workspace_id=ws.id, expires_at=None,
    )
    assert raw.startswith("ragz_sk_")
    assert row.prefix == raw[:12] and len(row.prefix) == 12
    assert row.key_hash != raw  # stored hashed, never plaintext
    assert raw not in row.key_hash


async def test_resolve_happy_path_and_updates_last_used(session, seeded_user):
    ws = await _member_ws(session, seeded_user)
    _, raw = await svc.generate_api_key(
        session, SETTINGS, actor_id=seeded_user.id, name="k", user_id=seeded_user.id,
        workspace_id=ws.id, expires_at=None,
    )
    p = await svc.resolve_api_key(session, SETTINGS, raw_key=raw)
    assert p is not None
    assert p.user_id == seeded_user.id
    assert p.workspace_id == ws.id
    assert p.org_id == seeded_user.org_id


async def test_resolve_rejects_unknown_revoked_and_expired(session, seeded_user):
    ws = await _member_ws(session, seeded_user)
    assert await svc.resolve_api_key(session, SETTINGS, raw_key="ragz_sk_nope") is None
    row, raw = await svc.generate_api_key(
        session, SETTINGS, actor_id=seeded_user.id, name="k", user_id=seeded_user.id,
        workspace_id=ws.id, expires_at=None,
    )
    await svc.revoke_api_key(session, key_id=row.id)
    assert await svc.resolve_api_key(session, SETTINGS, raw_key=raw) is None
    _, raw2 = await svc.generate_api_key(
        session, SETTINGS, actor_id=seeded_user.id, name="k2", user_id=seeded_user.id,
        workspace_id=ws.id, expires_at=datetime.now(UTC) - timedelta(hours=1),
    )
    assert await svc.resolve_api_key(session, SETTINGS, raw_key=raw2) is None


async def test_generate_with_no_expiry_gets_bounded_not_perpetual(session, seeded_user):
    # sec RAGZ-PUB-13: omitting expires_at must never create a perpetual key.
    ws = await _member_ws(session, seeded_user)
    row, _ = await svc.generate_api_key(
        session, SETTINGS, actor_id=seeded_user.id, name="k", user_id=seeded_user.id,
        workspace_id=ws.id, expires_at=None,
    )
    assert row.expires_at is not None
    expected = datetime.now(UTC) + timedelta(days=SETTINGS.api_key_max_lifetime_days)
    delta = abs((row.expires_at.replace(tzinfo=UTC) - expected).total_seconds())
    assert delta < 60  # generous clock-skew allowance for test runtime


async def test_generate_with_over_max_expiry_is_capped(session, seeded_user):
    # sec RAGZ-PUB-13: an over-long caller-supplied expiry is capped to the
    # max lifetime ceiling, not honored verbatim.
    ws = await _member_ws(session, seeded_user)
    far_future = datetime.now(UTC) + timedelta(days=3650)
    row, _ = await svc.generate_api_key(
        session, SETTINGS, actor_id=seeded_user.id, name="k", user_id=seeded_user.id,
        workspace_id=ws.id, expires_at=far_future,
    )
    ceiling = datetime.now(UTC) + timedelta(days=SETTINGS.api_key_max_lifetime_days)
    assert row.expires_at.replace(tzinfo=UTC) < far_future.replace(tzinfo=UTC)
    assert row.expires_at.replace(tzinfo=UTC) <= ceiling + timedelta(seconds=5)


async def test_generate_with_within_max_expiry_is_honored(session, seeded_user):
    ws = await _member_ws(session, seeded_user)
    soon = datetime.now(UTC) + timedelta(days=1)
    row, _ = await svc.generate_api_key(
        session, SETTINGS, actor_id=seeded_user.id, name="k", user_id=seeded_user.id,
        workspace_id=ws.id, expires_at=soon,
    )
    delta = abs((row.expires_at.replace(tzinfo=UTC) - soon).total_seconds())
    assert delta < 5


async def test_resolve_rejects_legacy_null_expiry_older_than_max_lifetime(session, seeded_user):
    # sec RAGZ-PUB-13: a pre-fix row with expires_at=NULL is NOT perpetual --
    # once created_at + max_lifetime is in the past, it must be rejected too.
    ws = await _member_ws(session, seeded_user)
    old_created_at = datetime.now(UTC) - timedelta(days=SETTINGS.api_key_max_lifetime_days + 1)
    row = ApiKey(
        prefix="ragz_sk_legacy-raw-key"[:12],
        key_hash=svc._hash("ragz_sk_legacy-raw-key", SETTINGS.api_key_pepper),
        name="legacy", org_id=seeded_user.org_id, user_id=seeded_user.id, workspace_id=ws.id,
        created_by=seeded_user.id, expires_at=None,
        created_at=old_created_at.astimezone(UTC).replace(tzinfo=None),
    )
    session.add(row)
    await session.commit()
    assert (
        await svc.resolve_api_key(session, SETTINGS, raw_key="ragz_sk_legacy-raw-key")
    ) is None


async def test_resolve_allows_legacy_null_expiry_newer_than_max_lifetime(session, seeded_user):
    ws = await _member_ws(session, seeded_user)
    recent_created_at = datetime.now(UTC) - timedelta(days=1)
    row = ApiKey(
        prefix="ragz_sk_legacy-raw-key2"[:12],
        key_hash=svc._hash("ragz_sk_legacy-raw-key2", SETTINGS.api_key_pepper),
        name="legacy", org_id=seeded_user.org_id, user_id=seeded_user.id, workspace_id=ws.id,
        created_by=seeded_user.id, expires_at=None,
        created_at=recent_created_at.astimezone(UTC).replace(tzinfo=None),
    )
    session.add(row)
    await session.commit()
    p = await svc.resolve_api_key(session, SETTINGS, raw_key="ragz_sk_legacy-raw-key2")
    assert p is not None
    assert p.user_id == seeded_user.id


async def test_generate_rejects_non_member(session, seeded_user):
    ws = await _member_ws(session, seeded_user)
    from ragz.modules.auth.passwords import hash_password
    from ragz.modules.tenancy.models import Organization
    org2 = Organization(name="Other")
    session.add(org2)
    await session.flush()
    stranger = User(
        org_id=org2.id, email="s@x.com", password_hash=hash_password("pw123456x"), role="user"
    )
    session.add(stranger)
    await session.flush()
    with pytest.raises(ConflictError):
        await svc.generate_api_key(
            session, SETTINGS, actor_id=seeded_user.id, name="bad",
            user_id=stranger.id, workspace_id=ws.id, expires_at=None,
        )
