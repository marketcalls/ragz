import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ragz.modules.auth.models import User
from ragz.modules.chat.models import Chat, Citation, Message
from ragz.modules.tenancy.models import Workspace


async def test_tree_rows_and_sibling_constraint(
    session: AsyncSession, seeded_user: User
) -> None:
    ws = Workspace(org_id=seeded_user.org_id, name="W")
    session.add(ws)
    await session.flush()
    chat = Chat(org_id=seeded_user.org_id, workspace_id=ws.id, user_id=seeded_user.id)
    session.add(chat)
    await session.flush()
    root = Message(chat_id=chat.id, parent_message_id=None, sibling_index=0,
                   role="user", content="q1")
    session.add(root)
    await session.flush()
    answer = Message(chat_id=chat.id, parent_message_id=root.id, sibling_index=0,
                     role="assistant", content="a1 [1]")
    session.add(answer)
    await session.flush()
    session.add(Citation(message_id=answer.id, document_id=ws.id, chunk_ref="d:1:0",
                         page=1, score=0.9, marker=1))
    await session.commit()
    assert chat.title == "New chat"

    dup = Message(chat_id=chat.id, parent_message_id=root.id, sibling_index=0,
                  role="assistant", content="dup")
    session.add(dup)
    with pytest.raises(IntegrityError):
        await session.flush()
    await session.rollback()


async def test_chat_summary_columns_default_none(session: AsyncSession, seeded_chat: Chat) -> None:
    assert seeded_chat.summary is None
    assert seeded_chat.summary_upto_message_id is None


async def test_chat_summary_columns_persist_round_trip(
    session: AsyncSession, seeded_chat: Chat
) -> None:
    root = Message(chat_id=seeded_chat.id, parent_message_id=None, sibling_index=0,
                   role="user", content="q1")
    session.add(root)
    await session.flush()
    seeded_chat.summary = "prior discussion about onboarding"
    seeded_chat.summary_upto_message_id = root.id
    await session.commit()

    chat_id = seeded_chat.id
    session.expunge(seeded_chat)
    reloaded = await session.get(Chat, chat_id)
    assert reloaded is not None
    assert reloaded.summary == "prior discussion about onboarding"
    assert reloaded.summary_upto_message_id == root.id
