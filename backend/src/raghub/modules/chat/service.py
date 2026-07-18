"""Chat service: conversation tree CRUD and invariants (spec 2.1).

Tree invariants live HERE, not in routes: roots are user-role, roles strictly
alternate parent->child, sibling_index is dense per (chat, parent).
"""

from collections import defaultdict
from collections.abc import AsyncIterator
from typing import Protocol
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from raghub.core.config import Settings
from raghub.core.db import naive_utc
from raghub.core.errors import ConflictError, NotFoundError, UpstreamError
from raghub.modules.chat.events import (
    CitationRef,
    SourceRef,
    SSEEvent,
    citations_event,
    done_event,
    error_event,
    retrieval_started_event,
    sources_event,
    token_event,
)
from raghub.modules.chat.llm import LLMDelta, LLMStreamer, LLMUsage
from raghub.modules.chat.models import Chat, Citation, Message
from raghub.modules.chat.prompting import (
    PromptSource,
    build_messages,
    parse_citation_markers,
)
from raghub.modules.documents import service as documents_service
from raghub.modules.models.models import Model  # type only; resolution stays in models service
from raghub.modules.retrieval.service import RetrievalResult
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


NO_ANSWER_TEXT = (
    "I couldn't find anything in this workspace's documents that answers that. "
    "The closest sources are shown, but none scored above the workspace's "
    "confidence threshold. Try rephrasing, or check that the relevant documents "
    "are uploaded and indexed."
)

_SNIPPET_CHARS = 300


class Retriever(Protocol):
    """Plan B's single retrieval code path, as an injectable seam for tests."""

    async def __call__(
        self,
        session: AsyncSession,
        ctx: TenantContext,
        workspace_id: UUID,
        query: str,
        top_k: int = 8,
    ) -> RetrievalResult: ...


def path_to_root(messages: list[Message], leaf: Message) -> list[Message]:
    """Ancestors of `leaf` (exclusive), ordered oldest -> newest."""
    by_id = {m.id: m for m in messages}
    path: list[Message] = []
    parent_id = leaf.parent_message_id
    while parent_id is not None:
        node = by_id[parent_id]
        path.append(node)
        parent_id = node.parent_message_id
    path.reverse()
    return path


async def _source_refs(
    session: AsyncSession, ctx: TenantContext, result: RetrievalResult
) -> list[SourceRef]:
    filenames: dict[UUID, str] = {}
    refs: list[SourceRef] = []
    for marker, chunk in enumerate(result.chunks, start=1):
        if chunk.document_id not in filenames:
            doc = await documents_service.get_document_checked(session, ctx, chunk.document_id)
            filenames[chunk.document_id] = doc.filename
        refs.append(
            SourceRef(
                marker=marker,
                document_id=str(chunk.document_id),
                filename=filenames[chunk.document_id],
                page=chunk.page,
                chunk_index=chunk.chunk_index,
                score=chunk.score,
                snippet=chunk.text[:_SNIPPET_CHARS],
            )
        )
    return refs


async def _persist_assistant(
    session: AsyncSession,
    ctx: TenantContext,
    chat: Chat,
    *,
    parent: Message,
    content: str,
    model_id: UUID | None,
    usage: LLMUsage | None,
    citations: list[CitationRef],
) -> Message:
    msg = await add_message(
        session, ctx, chat, role=ROLE_ASSISTANT, content=content, parent=parent,
        model_id=model_id,
        prompt_tokens=usage.prompt_tokens if usage else None,
        completion_tokens=usage.completion_tokens if usage else None,
    )
    for c in citations:
        session.add(
            Citation(
                message_id=msg.id, document_id=UUID(c.document_id),
                chunk_ref=c.chunk_ref, page=c.page, score=c.score, marker=c.marker,
            )
        )
    await session.commit()
    return msg


async def stream_reply(
    session: AsyncSession,
    ctx: TenantContext,
    *,
    chat: Chat,
    user_message: Message,
    model: Model,
    streamer: LLMStreamer,
    retriever: Retriever,
    settings: Settings,
) -> AsyncIterator[SSEEvent]:
    """The one SSE flow (spec 3.4): retrieval_started -> sources -> token* ->
    citations -> done. Used by both send and regenerate. `model` is resolved by
    the route (models_service.resolve_model) before any bytes are streamed."""
    yield retrieval_started_event()
    result = await retriever(session, ctx, chat.workspace_id, user_message.content)
    sources = await _source_refs(session, ctx, result)
    yield sources_event(sources)

    if result.no_answer:
        yield token_event(NO_ANSWER_TEXT)
        msg = await _persist_assistant(
            session, ctx, chat, parent=user_message, content=NO_ANSWER_TEXT,
            model_id=None, usage=None, citations=[],
        )
        yield citations_event([])
        yield done_event(message_id=str(msg.id), prompt_tokens=0,
                         completion_tokens=0, no_answer=True)
        return

    all_messages = await list_messages(session, chat.id)
    history = [(m.role, m.content) for m in path_to_root(all_messages, user_message)]
    prompt = build_messages(
        sources=[
            PromptSource(marker=s.marker, filename=s.filename, page=s.page,
                         text=result.chunks[s.marker - 1].text)
            for s in sources
        ],
        history=history,
        user_query=user_message.content,
        budget=settings.chat_context_token_budget,
    )

    parts: list[str] = []
    usage: LLMUsage | None = None
    try:
        async for item in streamer.stream(
            model=model.litellm_model_name, messages=prompt
        ):
            if isinstance(item, LLMDelta):
                parts.append(item.text)
                yield token_event(item.text)
            else:
                usage = item
    except UpstreamError as exc:
        # User message stays persisted; the client may retry (-> sibling).
        yield error_event(exc.detail or "LLM gateway error")
        return

    answer = "".join(parts)
    markers = parse_citation_markers(answer, len(sources))
    by_marker = {s.marker: s for s in sources}
    citation_refs = [
        CitationRef(
            marker=n,
            document_id=by_marker[n].document_id,
            chunk_ref=f"{by_marker[n].document_id}:{by_marker[n].page}:"
                      f"{by_marker[n].chunk_index}",
            page=by_marker[n].page,
            score=by_marker[n].score,
        )
        for n in markers
    ]
    msg = await _persist_assistant(
        session, ctx, chat, parent=user_message, content=answer, model_id=model.id,
        usage=usage, citations=citation_refs,
    )
    yield citations_event(citation_refs)
    yield done_event(
        message_id=str(msg.id),
        prompt_tokens=usage.prompt_tokens if usage else 0,
        completion_tokens=usage.completion_tokens if usage else 0,
        no_answer=False,
    )
