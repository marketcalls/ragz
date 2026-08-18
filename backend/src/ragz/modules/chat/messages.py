"""Message persistence, the branch tree, and per-message feedback.

Split out of chat/service.py (Phase 2 item 2 of the 2026-08-17 architecture
review). Sits above chat.chats and chat.attachments in the module's internal
dependency order -- it calls get_chat and list_attachments_by_message, which is
why both had to be extracted first -- and below the history/streaming code that
still lives in service.py.

ROLE_USER/ROLE_ASSISTANT are defined here rather than imported: they describe
what a message IS, and add_message is the only writer of the column.

Re-exported from chat.service; every existing caller keeps working unchanged.
"""

from collections import defaultdict
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ragz.core.db import naive_utc
from ragz.core.errors import ConflictError, NotFoundError
from ragz.modules.chat.attachments import list_attachments_by_message
from ragz.modules.chat.blocks import validate_blocks
from ragz.modules.chat.chats import _auto_title, get_chat
from ragz.modules.chat.models import (
    DEFAULT_CHAT_TITLE,
    Chat,
    ChatAttachment,
    Citation,
    Message,
    MessageFeedback,
)
from ragz.modules.chat.schemas import (
    AttachmentOut,
    ChatTreeOut,
    CitationOut,
    FeedbackOut,
    MessageNode,
)
from ragz.modules.tenancy.context import TenantContext

ROLE_USER = "user"
ROLE_ASSISTANT = "assistant"


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


async def list_feedback(
    session: AsyncSession, chat_id: UUID
) -> dict[UUID, MessageFeedback]:
    stmt = (
        select(MessageFeedback)
        .join(Message, Message.id == MessageFeedback.message_id)
        .where(Message.chat_id == chat_id)
    )
    return {fb.message_id: fb for fb in (await session.execute(stmt)).scalars()}


async def set_message_feedback(
    session: AsyncSession, ctx: TenantContext, message_id: UUID,
    *, rating: str, comment: str | None,
) -> MessageFeedback:
    _, msg = await get_message(session, ctx, message_id)  # NotFoundError if not caller's
    fb = (
        await session.execute(
            select(MessageFeedback).where(MessageFeedback.message_id == msg.id)
        )
    ).scalar_one_or_none()
    if fb is None:
        fb = MessageFeedback(
            message_id=msg.id, rating=rating, comment=comment, created_by=ctx.user_id,
        )
        session.add(fb)
    else:
        fb.rating = rating
        fb.comment = comment
    await session.commit()
    await session.refresh(fb)
    return fb


async def clear_message_feedback(
    session: AsyncSession, ctx: TenantContext, message_id: UUID
) -> None:
    _, msg = await get_message(session, ctx, message_id)  # NotFoundError if not caller's
    fb = (
        await session.execute(
            select(MessageFeedback).where(MessageFeedback.message_id == msg.id)
        )
    ).scalar_one_or_none()
    if fb is not None:
        await session.delete(fb)
        await session.commit()


def build_tree(
    messages: list[Message], citations: dict[UUID, list[Citation]],
    feedback: dict[UUID, MessageFeedback],
    attachments: dict[UUID, list[ChatAttachment]] | None = None,
) -> list[MessageNode]:
    children: dict[UUID | None, list[Message]] = defaultdict(list)
    for m in messages:
        children[m.parent_message_id].append(m)
    attachments = attachments or {}

    def node(m: Message) -> MessageNode:
        kids = sorted(children.get(m.id, []), key=lambda c: c.sibling_index)
        fb = feedback.get(m.id)
        msg_attachments = attachments.get(m.id, [])
        return MessageNode(
            id=m.id, parent_message_id=m.parent_message_id,
            sibling_index=m.sibling_index, role=m.role, content=m.content,
            model_id=m.model_id, prompt_tokens=m.prompt_tokens,
            completion_tokens=m.completion_tokens, created_at=m.created_at,
            stopped=m.stopped, no_answer=m.no_answer, grounding=m.grounding,
            grounding_score=m.grounding_score, completeness_score=m.completeness_score,
            validation_failed=m.validation_failed,
            citations=[CitationOut.model_validate(c) for c in citations.get(m.id, [])],
            feedback=FeedbackOut.model_validate(fb) if fb is not None else None,
            # In-chat generative UI (design 2026-08-15, §4): re-validated on
            # the way OUT too (not just trusted from storage) -- cheap,
            # never raises, and keeps history GET on the exact same Iron
            # Rule 5 boundary as the live SSE frame.
            blocks=validate_blocks(m.blocks_json) if m.blocks_json else None,
            # Transcript rendering (design 2026-08-15): metadata-only
            # (AttachmentOut has no bytes/storage_key/extracted_text field) --
            # never expose raw file content on the history read path.
            attachments=(
                [AttachmentOut.model_validate(a) for a in msg_attachments]
                if msg_attachments else None
            ),
            children=[node(k) for k in kids],
        )

    roots = sorted(children.get(None, []), key=lambda m: m.sibling_index)
    return [node(r) for r in roots]


async def get_chat_tree(
    session: AsyncSession, ctx: TenantContext, chat_id: UUID
) -> ChatTreeOut:
    chat = await get_chat(session, ctx, chat_id)
    messages = await list_messages(session, chat_id)
    citations = await list_citations(session, chat_id)
    feedback = await list_feedback(session, chat_id)
    attachments = await list_attachments_by_message(session, chat_id)
    return ChatTreeOut(
        id=chat.id, workspace_id=chat.workspace_id, title=chat.title,
        has_summary=chat.summary is not None,
        messages=build_tree(messages, citations, feedback, attachments),
    )


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
    stopped: bool = False,
    no_answer: bool = False,
    grounding: str = "documents",
    validation_failed: bool = False,
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
        stopped=stopped,
        no_answer=no_answer,
        grounding=grounding,
        validation_failed=validation_failed,
    )
    session.add(msg)
    if (
        role == ROLE_USER
        and parent is None
        and sibling_count == 0
        and chat.title == DEFAULT_CHAT_TITLE
    ):
        title = _auto_title(content)
        if title:
            chat.title = title
    chat.updated_at = naive_utc()  # explicit: onupdate only fires when a column changes
    await session.commit()
    return msg


async def add_user_message(
    session: AsyncSession,
    ctx: TenantContext,
    chat: Chat,
    content: str,
    *,
    parent_message_id: UUID | None = None,
    explicit: bool = False,
) -> Message:
    """Shared parent-resolution + persist for a new user turn (Task 4, DOC-9's
    sibling: factored out of `chats.py::send_message`'s inline block so
    `/external/v1/chat` doesn't duplicate it). Defaults (`parent_message_id`
    unset, `explicit=False`) match the external route's simpler contract --
    no edit/branch concept, always append to the active leaf. `send_message`
    passes its own body fields through unchanged, so its behavior is
    byte-identical to before this refactor."""
    messages = await list_messages(session, chat.id)
    parent = resolve_parent(messages, parent_message_id, explicit=explicit)
    return await add_message(session, ctx, chat, role=ROLE_USER, content=content, parent=parent)