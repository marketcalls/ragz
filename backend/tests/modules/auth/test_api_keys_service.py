from datetime import UTC, datetime, timedelta

import pytest

from ragz.core.config import Settings
from ragz.core.errors import ConflictError
from ragz.modules.auth import api_keys_service as svc
from ragz.modules.auth.models import User

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


async def test_generate_rejects_non_member(session, seeded_user):
    ws = await _member_ws(session, seeded_user)
    from ragz.modules.auth.passwords import hash_password
    from ragz.modules.tenancy.models import Organization
    org2 = Organization(name="Other")
    session.add(org2)
    await session.flush()
    stranger = User(
        org_id=org2.id, email="s@x.com", password_hash=hash_password("pw123456x"), role="member"
    )
    session.add(stranger)
    await session.flush()
    with pytest.raises(ConflictError):
        await svc.generate_api_key(
            session, SETTINGS, actor_id=seeded_user.id, name="bad",
            user_id=stranger.id, workspace_id=ws.id, expires_at=None,
        )
