"""Chat service: conversation tree CRUD and invariants (spec 2.1).

Tree invariants live HERE, not in routes: roots are user-role, roles strictly
alternate parent->child, sibling_index is dense per (chat, parent).
"""

from collections import defaultdict
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from raghub.core.db import naive_utc
from raghub.core.errors import ConflictError, NotFoundError
from raghub.modules.chat.models import Chat, Citation, Message
from raghub.modules.tenancy import service as tenancy_service
from raghub.modules.tenancy.context import TenantContext

ROLE_USER = "user"
ROLE_ASSISTANT = "assistant"


async def create_chat(
    session: AsyncSession, ctx: TenantContext, *, workspace_id: UUID, title: str | None = None
) -> Chat:
    await tenancy_service.get_workspace(session, ctx, workspace_id)
    chat = Chat(org_id=ctx.org_id, workspace_id=workspace_id, user_id=ctx.user_id)
    if title:
        chat.title = title
    session.add(chat)
    await session.commit()
    return chat


async def list_chats(session: AsyncSession, ctx: TenantContext) -> list[Chat]:
    stmt = (
        select(Chat)
        .where(Chat.org_id == ctx.org_id, Chat.user_id == ctx.user_id)
        .order_by(Chat.updated_at.desc())
    )
    return list((await session.execute(stmt)).scalars())


async def get_chat(session: AsyncSession, ctx: TenantContext, chat_id: UUID) -> Chat:
    chat = (
        await session.execute(
            select(Chat).where(
                Chat.id == chat_id, Chat.org_id == ctx.org_id, Chat.user_id == ctx.user_id
            )
        )
    ).scalar_one_or_none()
    if chat is None:
        raise NotFoundError("chat not found")
    return chat


async def rename_chat(
    session: AsyncSession, ctx: TenantContext, chat_id: UUID, title: str
) -> Chat:
    chat = await get_chat(session, ctx, chat_id)
    chat.title = title
    await session.commit()
    return chat


async def delete_chat(session: AsyncSession, ctx: TenantContext, chat_id: UUID) -> None:
    chat = await get_chat(session, ctx, chat_id)
    await session.delete(chat)  # messages + citations cascade at the DB layer
    await session.commit()


async def list_messages(session: AsyncSession, chat_id: UUID) -> list[Message]:
    stmt = select(Message).where(Message.chat_id == chat_id).order_by(Message.created_at)
    return list((await session.execute(stmt)).scalars())


async def get_message(
    session: AsyncSession, ctx: TenantContext, message_id: UUID
) -> tuple[Chat, Message]:
    msg = (
        await session.execute(select(Message).where(Message.id == message_id))
    ).scalar_one_or_none()
    if msg is None:
        raise NotFoundError("message not found")
    chat = await get_chat(session, ctx, msg.chat_id)  # NotFoundError if not the caller's
    return chat, msg


async def list_citations(
    session: AsyncSession, chat_id: UUID
) -> dict[UUID, list[Citation]]:
    stmt = (
        select(Citation)
        .join(Message, Message.id == Citation.message_id)
        .where(Message.chat_id == chat_id)
        .order_by(Citation.marker)
    )
    by_message: dict[UUID, list[Citation]] = defaultdict(list)
    for citation in (await session.execute(stmt)).scalars():
        by_message[citation.message_id].append(citation)
    return by_message


def active_leaf(messages: list[Message]) -> Message | None:
    """Follow the newest sibling (highest sibling_index) at every branch point."""
    children: dict[UUID | None, list[Message]] = defaultdict(list)
    for m in messages:
        children[m.parent_message_id].append(m)
    node: Message | None = None
    branch = children.get(None, [])
    while branch:
        node = max(branch, key=lambda m: m.sibling_index)
        branch = children.get(node.id, [])
    return node


def resolve_parent(
    messages: list[Message], parent_message_id: UUID | None, explicit: bool
) -> Message | None:
    """Resolve the parent for a NEW user message (send/edit semantics, spec 2.1).

    explicit=False -> append to the active leaf; if that leaf is a dangling user
    message (a previous stream died before the answer persisted), reuse ITS
    parent so the new message becomes a retry sibling.
    explicit=True  -> the caller chose: a message id (edit -> same parent as the
    edited sibling) or None (edit of a root message -> new root sibling).
    """
    if explicit:
        if parent_message_id is None:
            return None
        by_id = {m.id: m for m in messages}
        parent = by_id.get(parent_message_id)
        if parent is None:
            raise NotFoundError("parent message not found in this chat")
        return parent
    leaf = active_leaf(messages)
    if leaf is not None and leaf.role == ROLE_USER:
        by_id = {m.id: m for m in messages}
        return by_id.get(leaf.parent_message_id) if leaf.parent_message_id else None
    return leaf


async def add_message(
    session: AsyncSession,
    ctx: TenantContext,
    chat: Chat,
    *,
    role: str,
    content: str,
    parent: Message | None,
    model_id: UUID | None = None,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
) -> Message:
    if parent is None:
        if role != ROLE_USER:
            raise ConflictError("root messages must be user messages")
    elif parent.role == role:
        raise ConflictError("message roles must alternate")
    elif parent.chat_id != chat.id:
        raise NotFoundError("parent message not found in this chat")
    # serializes sibling_index computation per chat; NULL-parent roots have no unique backstop
    await session.execute(select(Chat).where(Chat.id == chat.id).with_for_update())
    sibling_count = (
        await session.execute(
            select(func.count())
            .select_from(Message)
            .where(
                Message.chat_id == chat.id,
                Message.parent_message_id == (parent.id if parent else None),
            )
        )
    ).scalar_one()
    msg = Message(
        chat_id=chat.id,
        parent_message_id=parent.id if parent else None,
        sibling_index=sibling_count,
        role=role,
        content=content,
        model_id=model_id,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
    )
    session.add(msg)
    chat.updated_at = naive_utc()  # explicit: onupdate only fires when a column changes
    await session.commit()
    return msg
