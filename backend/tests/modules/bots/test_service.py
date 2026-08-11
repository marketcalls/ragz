from uuid import uuid4

import pytest
from sqlalchemy import select

from ragz.core.errors import ConflictError, NotFoundError
from ragz.modules.auth.models import User
from ragz.modules.auth.passwords import hash_password
from ragz.modules.bots import service as svc
from ragz.modules.chat.models import Chat
from ragz.modules.secrets.models import Secret
from ragz.modules.tenancy.models import Organization, Workspace, WorkspaceMember


async def _member_ws(session, user: User) -> Workspace:
    ws = Workspace(org_id=user.org_id, name="BotWS")
    session.add(ws)
    await session.flush()
    session.add(WorkspaceMember(workspace_id=ws.id, user_id=user.id))
    await session.flush()
    return ws


async def test_create_stores_encrypted_secrets_not_plaintext(session, seeded_user, test_settings):
    ws = await _member_ws(session, seeded_user)
    row = await svc.create_integration(
        session, test_settings, actor_id=seeded_user.id, platform="telegram", name="support-bot",
        workspace_id=ws.id, user_id=seeded_user.id,
        token="123:ABC-tg-token", signing_secret="tg-secret",  # noqa: S106
    )
    assert row.platform == "telegram"
    assert row.webhook_id is not None
    rows = (await session.execute(select(Secret))).scalars().all()
    assert all("123:ABC-tg-token" not in (r.ciphertext or b"").decode("latin1") for r in rows)
    token = await svc.get_token(session, test_settings, integration_id=row.id)
    assert token == "123:ABC-tg-token"  # noqa: S105
    signing = await svc.get_signing_secret(session, test_settings, integration_id=row.id)
    assert signing == "tg-secret"


async def test_create_rejects_non_member(session, seeded_user, test_settings):
    ws = await _member_ws(session, seeded_user)
    org2 = Organization(name="Other")
    session.add(org2)
    await session.flush()
    stranger = User(
        org_id=org2.id, email="s@x.com",
        password_hash=hash_password("pw123456x"), role="user",  # noqa: S106
    )
    session.add(stranger)
    await session.flush()
    with pytest.raises(ConflictError):
        await svc.create_integration(
            session, test_settings, actor_id=seeded_user.id, platform="slack", name="bad",
            workspace_id=ws.id, user_id=stranger.id, token="t", signing_secret="s",  # noqa: S106
        )


async def test_resolve_by_webhook_happy_path(session, seeded_user, test_settings):
    ws = await _member_ws(session, seeded_user)
    row = await svc.create_integration(
        session, test_settings, actor_id=seeded_user.id, platform="discord", name="d",
        workspace_id=ws.id, user_id=seeded_user.id, token="t", signing_secret="s",  # noqa: S106
    )
    resolved = await svc.resolve_by_webhook(session, platform="discord", webhook_id=row.webhook_id)
    assert resolved.id == row.id


async def test_resolve_by_webhook_rejects_unknown_wrong_platform_and_disabled(
    session, seeded_user, test_settings
):
    ws = await _member_ws(session, seeded_user)
    row = await svc.create_integration(
        session, test_settings, actor_id=seeded_user.id, platform="slack", name="s",
        workspace_id=ws.id, user_id=seeded_user.id, token="t", signing_secret="s",  # noqa: S106
    )
    with pytest.raises(NotFoundError):
        await svc.resolve_by_webhook(session, platform="slack", webhook_id=uuid4())
    with pytest.raises(NotFoundError):  # right id, wrong platform in the URL
        await svc.resolve_by_webhook(session, platform="telegram", webhook_id=row.webhook_id)
    await svc.set_enabled(session, integration_id=row.id, enabled=False)
    with pytest.raises(NotFoundError):
        await svc.resolve_by_webhook(session, platform="slack", webhook_id=row.webhook_id)


async def test_delete_integration_removes_secrets(session, seeded_user, test_settings):
    ws = await _member_ws(session, seeded_user)
    row = await svc.create_integration(
        session, test_settings, actor_id=seeded_user.id, platform="telegram", name="t",
        workspace_id=ws.id, user_id=seeded_user.id, token="t", signing_secret="s",  # noqa: S106
    )
    integration_id = row.id
    await svc.delete_integration(
        session, test_settings, actor_id=seeded_user.id, integration_id=integration_id
    )
    with pytest.raises(NotFoundError):
        await svc.get_integration(session, integration_id=integration_id)
    with pytest.raises(NotFoundError):
        await svc.get_token(session, test_settings, integration_id=integration_id)


async def test_chat_mapping_get_or_create_roundtrip(session, seeded_user, test_settings):
    ws = await _member_ws(session, seeded_user)
    row = await svc.create_integration(
        session, test_settings, actor_id=seeded_user.id, platform="telegram", name="t",
        workspace_id=ws.id, user_id=seeded_user.id, token="t", signing_secret="s",  # noqa: S106
    )
    assert await svc.get_mapped_chat_id(
        session, integration_id=row.id, external_chat_id="chat-1"
    ) is None
    # chat_id must exist for the FK in Postgres (real FK-checked DB) -- seed a Chat row first.
    chat = Chat(org_id=seeded_user.org_id, workspace_id=ws.id, user_id=seeded_user.id)
    session.add(chat)
    await session.flush()
    chat_id = chat.id
    await svc.save_chat_mapping(
        session, integration_id=row.id, external_chat_id="chat-1", chat_id=chat_id
    )
    assert await svc.get_mapped_chat_id(
        session, integration_id=row.id, external_chat_id="chat-1"
    ) == chat_id
