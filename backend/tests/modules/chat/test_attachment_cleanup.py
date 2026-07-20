from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from raghub.modules.auth.models import User
from raghub.modules.chat.models import Chat, ChatAttachment
from raghub.modules.tenancy.models import Organization, Workspace


async def test_list_stale_attachments_only_returns_past_24h(session: AsyncSession) -> None:
    from raghub.core.db import naive_utc
    from raghub.modules.chat.service import list_stale_attachments

    # ChatAttachment.chat_id FKs to chats.id (and Chat itself FKs to
    # organizations/workspaces/users), so the brief's bare uuid4() chat_ids
    # would violate chat_attachments_chat_id_fkey -- built real rows here,
    # mirroring test_attachments.py's outer-failure test fixture setup.
    org = Organization(name="AttachCleanupOrg")
    session.add(org)
    await session.flush()
    user = User(
        org_id=org.id, email="cleanup@attachorg.com", password_hash="x",  # noqa: S106
        role="admin",
    )
    ws = Workspace(org_id=org.id, name="ws")
    session.add_all([user, ws])
    await session.flush()
    fresh_chat = Chat(org_id=org.id, workspace_id=ws.id, user_id=user.id)
    stale_chat = Chat(org_id=org.id, workspace_id=ws.id, user_id=user.id)
    session.add_all([fresh_chat, stale_chat])
    await session.flush()

    fresh = ChatAttachment(chat_id=fresh_chat.id, kind="document", filename="a.txt",
                            mime="text/plain", storage_key="k1", status="ready")
    stale = ChatAttachment(chat_id=stale_chat.id, kind="document", filename="b.txt",
                            mime="text/plain", storage_key="k2", status="ready")
    session.add_all([fresh, stale])
    await session.flush()
    stale.created_at = naive_utc() - timedelta(hours=25)
    await session.commit()

    cutoff = naive_utc() - timedelta(hours=24)
    results = await list_stale_attachments(session, cutoff)
    assert stale.id in {a.id for a in results}
    assert fresh.id not in {a.id for a in results}
