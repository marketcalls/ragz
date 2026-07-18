import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from raghub.core.errors import ConflictError, NotFoundError
from raghub.modules.auth.models import User
from raghub.modules.chat.models import Chat
from raghub.modules.chat.service import (
    active_leaf,
    add_message,
    create_chat,
    delete_chat,
    get_chat,
    list_chats,
    list_messages,
    rename_chat,
)
from raghub.modules.tenancy.context import TenantContext
from raghub.modules.tenancy.models import Workspace, WorkspaceMember


async def make_ctx(session: AsyncSession, user: User) -> tuple[TenantContext, Workspace]:
    ws = Workspace(org_id=user.org_id, name="W")
    session.add(ws)
    await session.flush()
    session.add(WorkspaceMember(workspace_id=ws.id, user_id=user.id))
    await session.commit()
    ctx = TenantContext(user_id=user.id, org_id=user.org_id, role=user.role,
                        workspace_ids=frozenset({ws.id}))
    return ctx, ws


async def build_turn(
    session: AsyncSession, ctx: TenantContext, chat: Chat, q: str, a: str, parent: object
) -> tuple[object, object]:
    user_msg = await add_message(session, ctx, chat, role="user", content=q, parent=parent)
    asst = await add_message(session, ctx, chat, role="assistant", content=a, parent=user_msg)
    return user_msg, asst


async def test_crud_and_scoping(session: AsyncSession, seeded_user: User) -> None:
    ctx, ws = await make_ctx(session, seeded_user)
    chat = await create_chat(session, ctx, workspace_id=ws.id)
    assert [c.id for c in await list_chats(session, ctx)] == [chat.id]
    renamed = await rename_chat(session, ctx, chat.id, "Q3 numbers")
    assert renamed.title == "Q3 numbers"

    other = TenantContext(user_id=ws.id, org_id=ctx.org_id, role="user",
                          workspace_ids=frozenset())
    with pytest.raises(NotFoundError):
        await get_chat(session, other, chat.id)  # another user never sees it

    await delete_chat(session, ctx, chat.id)
    assert await list_chats(session, ctx) == []


async def test_alternation_and_dense_siblings(
    session: AsyncSession, seeded_user: User
) -> None:
    """Verify message role alternation and dense sibling indices.

    Note: sibling_index density is protected by a row-level lock on the chat row
    (with_for_update()) in add_message, which serializes concurrent index computation.
    This test exercises the sequential case; true concurrency is hard to test reliably.
    """
    ctx, ws = await make_ctx(session, seeded_user)
    chat = await create_chat(session, ctx, workspace_id=ws.id)
    u1, a1 = await build_turn(session, ctx, chat, "q1", "a1", parent=None)

    with pytest.raises(ConflictError):  # user under user
        await add_message(session, ctx, chat, role="user", content="x", parent=u1)
    with pytest.raises(ConflictError):  # assistant at root
        await add_message(session, ctx, chat, role="assistant", content="x", parent=None)

    # Edit-and-resend: new user sibling at the ROOT gets the next index.
    u1b = await add_message(session, ctx, chat, role="user", content="q1 v2", parent=None)
    assert (u1b.sibling_index, u1.sibling_index) == (1, 0)
    # Regenerate: second assistant under the same user message.
    a1b = await add_message(session, ctx, chat, role="assistant", content="a1 v2", parent=u1)
    assert a1b.sibling_index == 1


async def test_active_leaf_follows_newest_siblings(
    session: AsyncSession, seeded_user: User
) -> None:
    ctx, ws = await make_ctx(session, seeded_user)
    chat = await create_chat(session, ctx, workspace_id=ws.id)
    u1, a1 = await build_turn(session, ctx, chat, "q1", "a1", parent=None)
    u2, a2 = await build_turn(session, ctx, chat, "q2", "a2", parent=a1)
    # Edit q2 -> sibling branch with its own answer; it becomes the active path.
    u2b, a2b = await build_turn(session, ctx, chat, "q2 v2", "a2 v2", parent=a1)
    msgs = await list_messages(session, chat.id)
    leaf = active_leaf(msgs)
    assert leaf is not None and leaf.id == a2b.id


async def test_membership_required_for_create(
    session: AsyncSession, seeded_user: User
) -> None:
    ctx, ws = await make_ctx(session, seeded_user)
    stranger = TenantContext(user_id=seeded_user.id, org_id=seeded_user.org_id,
                             role="user", workspace_ids=frozenset())
    with pytest.raises(NotFoundError):
        await create_chat(session, stranger, workspace_id=ws.id)
