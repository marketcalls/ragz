"""Chat service: conversation tree CRUD and invariants (spec 2.1).

Tree invariants live HERE, not in routes: roots are user-role, roles strictly
alternate parent->child, sibling_index is dense per (chat, parent).
"""

import asyncio
import contextlib
from collections import defaultdict
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol
from uuid import UUID

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from raghub.core.config import Settings, get_settings
from raghub.core.db import naive_utc
from raghub.core.errors import ConflictError, NotFoundError, UpstreamError
from raghub.modules.chat.agent import AgentGathered, AgentStep, run_agent_gather
from raghub.modules.chat.events import (
    CitationRef,
    SourceRef,
    SSEEvent,
    agent_step_event,
    citations_event,
    done_event,
    error_event,
    retrieval_started_event,
    sources_event,
    token_event,
)
from raghub.modules.chat.llm import LiteLLMStreamer, LLMCompleter, LLMDelta, LLMStreamer, LLMUsage
from raghub.modules.chat.models import DEFAULT_CHAT_TITLE, Chat, Citation, Message
from raghub.modules.chat.prompting import (
    PromptSource,
    build_conversational_messages,
    build_general_knowledge_messages,
    build_messages,
    count_tokens,
    fit_sources,
    parse_citation_markers,
    split_budget,
)
from raghub.modules.chat.router import classify_query, should_escalate
from raghub.modules.chat.schemas import ChatTreeOut, CitationOut, MessageNode
from raghub.modules.chat.validation import build_auditor_messages, parse_auditor_scores
from raghub.modules.chat.web import TAVILY_SECRET_NAME, WebResult, WebSearcher
from raghub.modules.documents import metadata as metadata_service
from raghub.modules.documents import service as documents_service
from raghub.modules.models.models import Model  # type only; resolution stays in models service
from raghub.modules.models.utility import get_utility_model
from raghub.modules.quotas import service as quota_service
from raghub.modules.retrieval.service import MetadataClause, RetrievalResult, RetrievedChunk
from raghub.modules.secrets import service as secrets_service
from raghub.modules.tenancy import service as tenancy_service
from raghub.modules.tenancy.context import TenantContext
from raghub.modules.tenancy.models import Workspace

ROLE_USER = "user"
ROLE_ASSISTANT = "assistant"

_TITLE_MAX_CHARS = 48


def _auto_title(content: str) -> str:
    """Derive a short, single-line chat title from the first user message.

    Collapses whitespace/newlines, then truncates to _TITLE_MAX_CHARS at a
    word boundary (falling back to a hard cut if there's no boundary),
    appending an ellipsis only when truncation actually happened.
    """
    text = " ".join(content.split())
    if len(text) <= _TITLE_MAX_CHARS:
        return text
    truncated = text[:_TITLE_MAX_CHARS].rsplit(" ", 1)[0]
    return f"{truncated}…"


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


async def list_chats(
    session: AsyncSession, ctx: TenantContext, *, workspace_id: UUID | None = None
) -> list[Chat]:
    stmt = (
        select(Chat)
        .where(Chat.org_id == ctx.org_id, Chat.user_id == ctx.user_id)
        .order_by(Chat.updated_at.desc())
    )
    if workspace_id is not None:
        stmt = stmt.where(Chat.workspace_id == workspace_id)
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


def build_tree(
    messages: list[Message], citations: dict[UUID, list[Citation]]
) -> list[MessageNode]:
    children: dict[UUID | None, list[Message]] = defaultdict(list)
    for m in messages:
        children[m.parent_message_id].append(m)

    def node(m: Message) -> MessageNode:
        kids = sorted(children.get(m.id, []), key=lambda c: c.sibling_index)
        return MessageNode(
            id=m.id, parent_message_id=m.parent_message_id,
            sibling_index=m.sibling_index, role=m.role, content=m.content,
            model_id=m.model_id, prompt_tokens=m.prompt_tokens,
            completion_tokens=m.completion_tokens, created_at=m.created_at,
            stopped=m.stopped, no_answer=m.no_answer, grounding=m.grounding,
            grounding_score=m.grounding_score, completeness_score=m.completeness_score,
            citations=[CitationOut.model_validate(c) for c in citations.get(m.id, [])],
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
    return ChatTreeOut(
        id=chat.id, workspace_id=chat.workspace_id, title=chat.title,
        messages=build_tree(messages, citations),
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


NO_ANSWER_TEXT = (
    "I couldn't find anything in this workspace's documents that answers that. "
    "The closest sources are shown, but none scored above the workspace's "
    "confidence threshold. Try rephrasing, or check that the relevant documents "
    "are uploaded and indexed."
)

_SNIPPET_CHARS = 300


class Retriever(Protocol):
    """Plan B's single retrieval code path, as an injectable seam for tests.
    Plan I: metadata_clauses pass-through for the agent's search_by_metadata
    tool (real retrieve() already accepts it)."""

    async def __call__(
        self,
        session: AsyncSession,
        ctx: TenantContext,
        workspace_id: UUID,
        query: str,
        top_k: int | None = None,
        metadata_clauses: Sequence[MetadataClause] | None = None,
    ) -> RetrievalResult: ...


class ChunkReader(Protocol):
    """Plan E seam: tenant-filtered chunk reads (pinned docs + citation
    backfill). Implemented by modules/retrieval.RetrievalChunkReader; faked in
    tests. Mirrors the Retriever/LLMStreamer injection pattern."""

    async def list_document_chunks(
        self, ctx: TenantContext, workspace_id: UUID, document_id: UUID
    ) -> list[RetrievedChunk]: ...

    async def get_chunks_by_refs(
        self, ctx: TenantContext, workspace_id: UUID, refs: Sequence[str]
    ) -> list[RetrievedChunk]: ...


def merge_chunks(*groups: Sequence[RetrievedChunk]) -> list[RetrievedChunk]:
    """Concatenate priority-ordered chunk groups (pinned, retrieved, backfilled),
    deduping on (document_id, page, chunk_index) — first occurrence wins, so a
    retrieved duplicate of a pinned chunk collapses into the pinned entry
    (AnythingLLM's pinned-vs-search dedupe)."""
    seen: set[tuple[UUID, int, int]] = set()
    out: list[RetrievedChunk] = []
    for group in groups:
        for c in group:
            key = (c.document_id, c.page, c.chunk_index)
            if key not in seen:
                seen.add(key)
                out.append(c)
    return out


def cap_chunks_by_tokens(
    chunks: Sequence[RetrievedChunk], max_tokens: int, model_hint: str | None
) -> list[RetrievedChunk]:
    """Prefix of `chunks` whose raw texts fit max_tokens — the pinned-docs cap
    (half the sources share) so pinned material can never starve retrieval."""
    kept: list[RetrievedChunk] = []
    used = 0
    for c in chunks:
        cost = count_tokens(c.text, model_hint)
        if used + cost > max_tokens:
            break
        kept.append(c)
        used += cost
    return kept


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


async def _previous_citation_refs(
    session: AsyncSession, messages: list[Message], user_message: Message
) -> list[str]:
    """chunk_refs cited by the NEAREST assistant ancestor of this turn (walks
    the active branch only — an edit's new branch backfills from its own
    lineage). Empty when this is the first exchange or the previous answer had
    no citations (e.g. a small-talk or refused turn)."""
    for ancestor in reversed(path_to_root(messages, user_message)):
        if ancestor.role == ROLE_ASSISTANT:
            rows = (
                await session.execute(
                    select(Citation)
                    .where(Citation.message_id == ancestor.id)
                    .order_by(Citation.marker)
                )
            ).scalars()
            return [c.chunk_ref for c in rows]
    return []


async def _source_refs(
    session: AsyncSession, ctx: TenantContext, chunks: Sequence[RetrievedChunk]
) -> list[SourceRef]:
    # (filename, version) from the SAME get_document_checked fetch: version is
    # the document ROW's own version (each version lineage is its own row,
    # DOC-5), the canonical answer - not a chunk-local guess.
    docs: dict[UUID, tuple[str, int]] = {}
    refs: list[SourceRef] = []
    for marker, chunk in enumerate(chunks, start=1):
        if chunk.document_id not in docs:
            doc = await documents_service.get_document_checked(session, ctx, chunk.document_id)
            docs[chunk.document_id] = (doc.filename, doc.version)
        filename, doc_version = docs[chunk.document_id]
        refs.append(
            SourceRef(
                marker=marker,
                document_id=str(chunk.document_id),
                filename=filename,
                page=chunk.page,
                chunk_index=chunk.chunk_index,
                score=chunk.score,
                snippet=chunk.text[:_SNIPPET_CHARS],
                section=chunk.section,
                version=doc_version,
                url=None,
            )
        )
    return refs


async def _prepare_sources(
    session: AsyncSession,
    ctx: TenantContext,
    chunks: Sequence[RetrievedChunk],
    *,
    max_tokens: int,
    model_hint: str | None,
    web_results: Sequence[WebResult] = (),
) -> tuple[list[SourceRef], list[PromptSource]]:
    """Marker assignment + budget fitting in one place, so the `sources` SSE
    frame lists exactly the blocks the model receives (markers stay dense:
    fit_sources keeps a prefix). Web hits (D7/Task 11) are appended AFTER the
    document chunks with continuing markers -- documents outrank the web in
    the budget fit (fit_sources keeps a PREFIX, so a web source only survives
    once every document source already fits)."""
    refs = await _source_refs(session, ctx, chunks)
    prompt_sources = [
        PromptSource(marker=r.marker, filename=r.filename, page=r.page,
                     text=chunks[i].text, section=r.section)
        for i, r in enumerate(refs)
    ]
    next_marker = len(refs) + 1
    for i, w in enumerate(web_results):
        marker = next_marker + i
        refs.append(
            SourceRef(
                marker=marker, document_id="", filename=w.title, page=0,
                chunk_index=0, score=0.0, snippet=w.snippet[:_SNIPPET_CHARS],
                section=None, version=0, url=w.url,
            )
        )
        prompt_sources.append(
            PromptSource(marker=marker, filename=w.title, page=0, text=w.snippet, url=w.url)
        )
    kept = fit_sources(prompt_sources, max_tokens, model_hint)
    return refs[: len(kept)], kept


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
    no_answer: bool = False,
    grounding: str = "documents",
) -> Message:
    msg = await add_message(
        session, ctx, chat, role=ROLE_ASSISTANT, content=content, parent=parent,
        model_id=model_id,
        prompt_tokens=usage.prompt_tokens if usage else None,
        completion_tokens=usage.completion_tokens if usage else None,
        no_answer=no_answer,
        grounding=grounding,
    )
    for c in citations:
        session.add(
            Citation(
                message_id=msg.id,
                document_id=UUID(c.document_id) if c.document_id else None,
                chunk_ref=c.chunk_ref, page=c.page, score=c.score, marker=c.marker,
                section=c.section, version=c.version, url=c.url,
            )
        )
    await session.commit()
    return msg


def _completer_for_audit(settings: Settings) -> LiteLLMStreamer:
    """Own gateway client per audit run - the worker has no request-scoped
    app.state to borrow one from (mirrors LiteLLMStreamer's construction in
    chats.py's _streamer, minus the per-user virtual key: audit calls are
    platform overhead, not a member's own usage)."""
    return LiteLLMStreamer(base_url=settings.litellm_url, master_key=settings.litellm_master_key)


async def audit_message(session: AsyncSession, message_id: UUID) -> bool:
    """Phase 3 Auditor (§3): scores ONE already-persisted message. No ctx -
    this runs from a worker-owned session with no request-scoped tenant
    context; it only ever touches the single message_id the route already
    resolved inside a real, ACL-checked request, so it needs no additional
    tenant filtering of its own. Returns False (no-op, never raises) when
    there is no utility model, the message is gone, or grounding != 'documents'
    (nothing meaningful to check citations against on conversational/
    general-knowledge/no-answer turns)."""
    utility_model = await get_utility_model(session)
    if utility_model is None:
        return False
    msg = (
        await session.execute(select(Message).where(Message.id == message_id))
    ).scalar_one_or_none()
    if msg is None or msg.grounding != "documents" or msg.no_answer:
        return False
    user_msg = (
        await session.execute(select(Message).where(Message.id == msg.parent_message_id))
    ).scalar_one_or_none()
    question = user_msg.content if user_msg else ""
    citations = (
        await session.execute(
            select(Citation).where(Citation.message_id == msg.id).order_by(Citation.marker)
        )
    ).scalars()
    sources = [
        PromptSource(marker=c.marker, filename=c.chunk_ref, page=c.page, text="", section=c.section)
        for c in citations
    ]
    settings = get_settings()
    completer = _completer_for_audit(settings)
    completion = await completer.complete(
        model=utility_model.litellm_model_name,
        messages=build_auditor_messages(question=question, answer=msg.content, sources=sources),
    )
    scores = parse_auditor_scores(completion.text)
    if scores is None:
        return False
    msg.grounding_score = scores.grounding_score
    msg.completeness_score = scores.completeness_score
    chat = await session.get(Chat, msg.chat_id)
    assert chat is not None  # FK guarantees the parent chat row exists
    await quota_service.record_usage(
        session, org_id=chat.org_id, user_id=chat.user_id, model_id=utility_model.id,
        feature="validation", prompt_tokens=completion.usage.prompt_tokens,
        completion_tokens=completion.usage.completion_tokens,
    )
    await session.commit()
    return True


@dataclass(frozen=True)
class WorstAnswerRow:
    message_id: UUID
    chat_id: UUID
    content_snippet: str
    grounding_score: float | None
    completeness_score: float | None
    created_at: datetime


@dataclass(frozen=True)
class AnswerQualitySummary:
    audited_count: int
    avg_grounding_score: float | None
    avg_completeness_score: float | None
    low_score_count: int  # grounding_score < 0.5 OR completeness_score < 0.5
    worst: list[WorstAnswerRow]


_SNIPPET_CHARS_QUALITY = 200
_LOW_SCORE_THRESHOLD = 0.5


async def answer_quality_summary(
    session: AsyncSession, ctx: TenantContext, *, days: int, limit: int = 10
) -> AnswerQualitySummary:
    """Phase 3 Auditor surfacing (§3): org-scoped average scores + the
    lowest-scoring answers, for the admin dashboard tile/table. Only
    audited messages (grounding_score IS NOT NULL) count."""
    cutoff = naive_utc() - timedelta(days=days)
    base = (
        select(Message)
        .join(Chat, Chat.id == Message.chat_id)
        .where(
            Chat.org_id == ctx.org_id,
            Message.grounding_score.is_not(None),
            Message.created_at >= cutoff,
        )
    )
    rows = list((await session.execute(base)).scalars())
    audited_count = len(rows)
    if audited_count == 0:
        return AnswerQualitySummary(0, None, None, 0, [])
    avg_grounding = sum(m.grounding_score for m in rows) / audited_count  # type: ignore[misc]
    avg_completeness = sum(m.completeness_score for m in rows) / audited_count  # type: ignore[misc]
    low_score_count = sum(
        1 for m in rows
        if (m.grounding_score or 0) < _LOW_SCORE_THRESHOLD
        or (m.completeness_score or 0) < _LOW_SCORE_THRESHOLD
    )
    worst = sorted(
        rows, key=lambda m: ((m.grounding_score or 0) + (m.completeness_score or 0)) / 2
    )[:limit]
    return AnswerQualitySummary(
        audited_count=audited_count,
        avg_grounding_score=avg_grounding,
        avg_completeness_score=avg_completeness,
        low_score_count=low_score_count,
        worst=[
            WorstAnswerRow(
                message_id=m.id, chat_id=m.chat_id,
                content_snippet=m.content[:_SNIPPET_CHARS_QUALITY],
                grounding_score=m.grounding_score, completeness_score=m.completeness_score,
                created_at=m.created_at,
            )
            for m in worst
        ],
    )


_STOP_PERSISTS: set[asyncio.Task[None]] = set()


def persist_stopped_detached(
    session_factory: async_sessionmaker[AsyncSession],
    ctx: TenantContext,
    *,
    chat_id: UUID,
    user_message_id: UUID,
    content: str,
    model_id: UUID | None,
) -> asyncio.Task[None]:
    """Persist a partial answer after client abort (G2).

    Runs on a task + session that OUTLIVE the dying SSE request: by the time
    cancellation unwinds the generator, the request-scoped session may already
    be closing, and awaiting on the cancelled task itself would be re-cancelled.
    Tracked in _STOP_PERSISTS so tests (and a future shutdown hook) can await
    completion; failures are logged, never raised — losing a partial answer is
    acceptable, crashing teardown is not.
    """

    async def _persist() -> None:
        try:
            async with session_factory() as session:
                chat = await get_chat(session, ctx, chat_id)
                messages = await list_messages(session, chat_id)
                parent = next(m for m in messages if m.id == user_message_id)
                await add_message(
                    session, ctx, chat, role=ROLE_ASSISTANT, content=content,
                    parent=parent, model_id=model_id, stopped=True,
                )
        except Exception:
            structlog.get_logger().error("stop_persist_failed", exc_info=True)

    task = asyncio.get_running_loop().create_task(_persist())
    _STOP_PERSISTS.add(task)
    task.add_done_callback(_STOP_PERSISTS.discard)
    return task


async def stream_reply(
    session: AsyncSession,
    ctx: TenantContext,
    *,
    chat: Chat,
    workspace: Workspace,
    user_message: Message,
    model: Model,
    streamer: LLMStreamer,
    retriever: Retriever,
    chunk_reader: ChunkReader,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    completer: LLMCompleter | None = None,
    web_searcher: WebSearcher | None = None,
) -> AsyncIterator[SSEEvent]:
    """The one SSE flow (spec 3.4): retrieval_started -> sources -> token* ->
    citations -> done. Used by both send and regenerate. `model` is resolved by
    the route (models_service.resolve_model) before any bytes are streamed.

    Phase-1 CHAT-3 router seam: `classify_query` (router.py) runs first on the
    user's message. "conversational" turns (small talk) skip retrieval
    entirely - no retrieval_started/sources frames, no <data> blocks in the
    prompt, and the persisted assistant reply carries no citations. Anything
    else takes the retrieval path below, unchanged. Phase 3 may swap
    classify_query for a smarter router without touching this function.
    """
    conversational = classify_query(user_message.content) == "conversational"
    if not conversational:
        yield retrieval_started_event()

    streamed_parts: list[str] = []
    try:
        if conversational:
            all_messages = await list_messages(session, chat.id)
            history = [
                (m.role, m.content) for m in path_to_root(all_messages, user_message)
            ]
            prompt = build_conversational_messages(
                history=history,
                user_query=user_message.content,
                budget=settings.chat_context_token_budget,
                system_prompt_override=workspace.system_prompt_override,
                model_hint=model.litellm_model_name,
            )

            convo_usage: LLMUsage | None = None
            # aclosing (not a bare async-for): on client abort, GeneratorExit
            # is thrown into THIS frame at the `yield token_event` below, not
            # into streamer.stream()'s frame - a bare async-for only drops
            # the inner generator's last reference, and asyncio's asyncgen
            # finalizer then closes it on a LATER loop iteration (not
            # synchronously), so the upstream LLM call would leak open past
            # this request's teardown. aclosing's __aexit__ awaits aclose()
            # deterministically, in the same unwind.
            async with contextlib.aclosing(
                streamer.stream(model=model.litellm_model_name, messages=prompt)
            ) as llm_stream:
                async for item in llm_stream:
                    if isinstance(item, LLMDelta):
                        streamed_parts.append(item.text)
                        yield token_event(item.text)
                    else:
                        convo_usage = item

            convo_answer = "".join(streamed_parts)
            msg = await _persist_assistant(
                session, ctx, chat, parent=user_message, content=convo_answer,
                model_id=model.id, usage=convo_usage, citations=[],
            )
            # Persist succeeded: clear the buffer BEFORE any further
            # await/yield point (record_usage, citations_event, done_event)
            # so a late abort in that window can't re-persist the same
            # content as a second assistant row (review round 1, finding 1).
            streamed_parts.clear()
            if convo_usage is not None:
                await quota_service.record_usage(
                    session, org_id=ctx.org_id, user_id=ctx.user_id, model_id=model.id,
                    feature="chat", prompt_tokens=convo_usage.prompt_tokens,
                    completion_tokens=convo_usage.completion_tokens,
                )
            yield citations_event([])
            yield done_event(
                message_id=str(msg.id),
                prompt_tokens=convo_usage.prompt_tokens if convo_usage else 0,
                completion_tokens=convo_usage.completion_tokens if convo_usage else 0,
                no_answer=False, grounding="documents",
            )
            return

        model_hint = model.litellm_model_name
        split = split_budget(settings.chat_context_token_budget)
        query_cost = count_tokens(user_message.content, model_hint)
        # The budget actually available to render sources, AFTER the user's
        # question is paid for. Capping pinned chunks against this (not the
        # raw split.sources share) means a long user message can't let pinned
        # docs eat the entire rendered budget and starve retrieval.
        sources_budget = max(split.sources - query_cost, 0)

        all_messages = await list_messages(session, chat.id)

        pinned_chunks: list[RetrievedChunk] = []
        for doc in await documents_service.list_pinned_documents(
            session, ctx, chat.workspace_id
        ):
            pinned_chunks.extend(
                await chunk_reader.list_document_chunks(ctx, chat.workspace_id, doc.id)
            )
        pinned_chunks = cap_chunks_by_tokens(
            pinned_chunks, sources_budget // 2, model_hint
        )
        pinned_keys = {(str(c.document_id), c.page, c.chunk_index) for c in pinned_chunks}

        # Phase 3 escalation (design §1): pre-trigger runs BEFORE the single
        # retrieval shot (should_escalate on the raw question + this
        # workspace's metadata field names); post-trigger fires only when
        # that single shot came back weak AND the workspace permits a
        # general-knowledge fallback AND the workspace actually has indexed
        # content to loop over (empty workspaces fall straight through).
        # completer=None (no injected/derived LLMCompleter) disables
        # escalation entirely -- every pre-Plan-I call site is unaffected.
        field_names: list[str] = []
        if completer is not None:
            field_names = [
                f.name for f in await metadata_service.list_fields(session, ctx, chat.workspace_id)
            ]
        pre_escalate = completer is not None and should_escalate(
            user_message.content, field_names
        )
        result = (
            RetrievalResult(chunks=[], no_answer=True)
            if pre_escalate  # the loop's first search subsumes the single shot
            else await retriever(session, ctx, chat.workspace_id, user_message.content)
        )
        run_loop = pre_escalate or (
            completer is not None
            and result.no_answer
            and workspace.fallback_policy == "general_knowledge"
            and await documents_service.has_indexed_documents(session, ctx, chat.workspace_id)
        )
        agent_prompt_tokens = agent_completion_tokens = 0
        web_hits: list[WebResult] = []
        if run_loop and completer is not None:
            # D7/Task 11: the web_search tool is offered to the planner only
            # when a searcher is injected AND the workspace opted in AND its
            # policy isn't decline AND the tavily secret is actually stored
            # (existence-only check -- decryption happens inside the
            # searcher itself, on actual use).
            use_web = (
                web_searcher is not None
                and workspace.web_search_enabled
                and workspace.fallback_policy != "decline"
                and bool(await secrets_service.existing_secret_names(session, [TAVILY_SECRET_NAME]))
            )
            gathered: AgentGathered | None = None
            async for gather_item in run_agent_gather(
                session, ctx, workspace=workspace, question=user_message.content,
                model=model, completer=completer, retriever=retriever,
                chunk_reader=chunk_reader, web_searcher=web_searcher if use_web else None,
                metadata_field_names=field_names,
            ):
                if isinstance(gather_item, AgentStep):
                    yield agent_step_event(
                        n=gather_item.n, tool=gather_item.tool, query=gather_item.query
                    )
                else:
                    gathered = gather_item
            if gathered is not None:
                agent_prompt_tokens = gathered.prompt_tokens
                agent_completion_tokens = gathered.completion_tokens
                web_hits = gathered.web_results
                result = RetrievalResult(
                    # Loop findings outrank the stale weak single shot for the
                    # budget fit; merge_chunks dedupes on chunk identity.
                    chunks=merge_chunks(gathered.chunks, result.chunks),
                    no_answer=not gathered.grounded,
                )

        backfilled: list[RetrievedChunk] = []
        if len(result.chunks) < workspace.top_k:
            prev_refs = await _previous_citation_refs(session, all_messages, user_message)
            if prev_refs:
                backfilled = await chunk_reader.get_chunks_by_refs(
                    ctx, chat.workspace_id, prev_refs
                )
        backfilled_keys = {(str(c.document_id), c.page, c.chunk_index) for c in backfilled}
        merged = merge_chunks(pinned_chunks, result.chunks, backfilled)

        sources, kept_sources = await _prepare_sources(
            session, ctx, merged, max_tokens=sources_budget, model_hint=model_hint,
            web_results=web_hits,
        )

        # no_answer is decided AFTER the final fit: pinned/backfilled material
        # only counts as grounding if it actually survived into kept_sources
        # (the rendered prompt). If the fit dropped every pinned/backfilled
        # chunk (e.g. an exhausted budget), the original retrieval verdict
        # stands rather than streaming an answer over an effectively empty
        # sources frame. And regardless of that verdict: an empty kept_sources
        # means there is nothing left to ground an answer in at all (e.g. a
        # huge query that eats the whole sources budget), so the no-answer
        # path fires unconditionally rather than trusting a stale
        # result.no_answer=False computed before the fit ran.
        kept_keys = {(s.document_id, s.page, s.chunk_index) for s in sources}
        no_answer = not kept_sources or (
            result.no_answer and not ((pinned_keys | backfilled_keys) & kept_keys)
        )

        if no_answer and workspace.fallback_policy == "general_knowledge":
            # Design §1: weak retrieval + permissive policy -> general-knowledge
            # answer. No <data>, no sources/citations frames, nothing to cite.
            history = [
                (m.role, m.content) for m in path_to_root(all_messages, user_message)
            ]
            prompt = build_general_knowledge_messages(
                history=history, user_query=user_message.content,
                budget=settings.chat_context_token_budget,
                system_prompt_override=workspace.system_prompt_override,
                model_hint=model_hint,
            )
            gk_usage: LLMUsage | None = None
            # aclosing: same deterministic-cleanup-on-abort reasoning as the
            # conversational branch.
            async with contextlib.aclosing(
                streamer.stream(model=model.litellm_model_name, messages=prompt)
            ) as llm_stream:
                async for item in llm_stream:
                    if isinstance(item, LLMDelta):
                        streamed_parts.append(item.text)
                        yield token_event(item.text)
                    else:
                        gk_usage = item
            if agent_prompt_tokens or agent_completion_tokens:
                gk_usage = LLMUsage(
                    prompt_tokens=(gk_usage.prompt_tokens if gk_usage else 0)
                    + agent_prompt_tokens,
                    completion_tokens=(gk_usage.completion_tokens if gk_usage else 0)
                    + agent_completion_tokens,
                )
            msg = await _persist_assistant(
                session, ctx, chat, parent=user_message,
                content="".join(streamed_parts), model_id=model.id,
                usage=gk_usage, citations=[], grounding="general",
            )
            streamed_parts.clear()  # same duplicate-row guard as the other branches
            if gk_usage is not None:
                await quota_service.record_usage(
                    session, org_id=ctx.org_id, user_id=ctx.user_id, model_id=model.id,
                    feature="chat", prompt_tokens=gk_usage.prompt_tokens,
                    completion_tokens=gk_usage.completion_tokens,
                )
            yield done_event(
                message_id=str(msg.id),
                prompt_tokens=gk_usage.prompt_tokens if gk_usage else 0,
                completion_tokens=gk_usage.completion_tokens if gk_usage else 0,
                no_answer=False, grounding="general",
            )
            return

        yield sources_event(sources)

        if no_answer:  # decline policy: pre-Plan-I behavior, byte-identical
            # -- except: the ledger still sees planner tokens spent on a
            # loop that ultimately failed to ground the turn (spike
            # contract: full spend recorded on every path).
            yield token_event(NO_ANSWER_TEXT)
            msg = await _persist_assistant(
                session, ctx, chat, parent=user_message, content=NO_ANSWER_TEXT,
                model_id=None, usage=None, citations=[], no_answer=True,
            )
            if agent_prompt_tokens or agent_completion_tokens:
                await quota_service.record_usage(
                    session, org_id=ctx.org_id, user_id=ctx.user_id, model_id=model.id,
                    feature="chat", prompt_tokens=agent_prompt_tokens,
                    completion_tokens=agent_completion_tokens,
                )
            yield citations_event([])
            yield done_event(message_id=str(msg.id), prompt_tokens=agent_prompt_tokens,
                             completion_tokens=agent_completion_tokens, no_answer=True,
                             grounding="documents")
            return

        history = [(m.role, m.content) for m in path_to_root(all_messages, user_message)]
        prompt = build_messages(
            sources=kept_sources,
            history=history,
            user_query=user_message.content,
            budget=settings.chat_context_token_budget,
            system_prompt_override=workspace.system_prompt_override,
            model_hint=model_hint,
        )

        usage: LLMUsage | None = None
        # See the aclosing note in the conversational branch above - same
        # deterministic-cleanup-on-abort reasoning applies here.
        async with contextlib.aclosing(
            streamer.stream(model=model.litellm_model_name, messages=prompt)
        ) as llm_stream:
            async for item in llm_stream:
                if isinstance(item, LLMDelta):
                    streamed_parts.append(item.text)
                    yield token_event(item.text)
                else:
                    usage = item

        if agent_prompt_tokens or agent_completion_tokens:
            usage = LLMUsage(
                prompt_tokens=(usage.prompt_tokens if usage else 0) + agent_prompt_tokens,
                completion_tokens=(usage.completion_tokens if usage else 0)
                + agent_completion_tokens,
            )

        answer = "".join(streamed_parts)
        markers = parse_citation_markers(answer, len(sources))
        by_marker = {s.marker: s for s in sources}
        citation_refs = [
            CitationRef(
                marker=n,
                document_id=by_marker[n].document_id,
                chunk_ref=(
                    f"web:{by_marker[n].url}"
                    if by_marker[n].url
                    else f"{by_marker[n].document_id}:{by_marker[n].page}:"
                         f"{by_marker[n].chunk_index}"
                ),
                page=by_marker[n].page,
                score=by_marker[n].score,
                section=by_marker[n].section,
                version=by_marker[n].version,
                url=by_marker[n].url,
            )
            for n in markers
        ]
        msg = await _persist_assistant(
            session, ctx, chat, parent=user_message, content=answer, model_id=model.id,
            usage=usage, citations=citation_refs,
        )
        # Same rationale as the conversational branch above: clear before the
        # next await/yield point so a late abort can't duplicate this row.
        streamed_parts.clear()
        if usage is not None:
            await quota_service.record_usage(
                session, org_id=ctx.org_id, user_id=ctx.user_id, model_id=model.id,
                feature="chat", prompt_tokens=usage.prompt_tokens,
                completion_tokens=usage.completion_tokens,
            )
        yield citations_event(citation_refs)
        yield done_event(
            message_id=str(msg.id),
            prompt_tokens=usage.prompt_tokens if usage else 0,
            completion_tokens=usage.completion_tokens if usage else 0,
            no_answer=False, grounding="documents",
        )
    except UpstreamError:
        # User message stays persisted; the client may retry (-> sibling).
        # Do not forward exc.detail as it can carry raw gateway body text.
        yield error_event("the language model gateway failed")
        return
    except Exception:
        # Log unexpected errors but yield generic message to client.
        structlog.get_logger().error("stream_failed", exc_info=True)
        yield error_event("streaming failed unexpectedly")
        return
    except (asyncio.CancelledError, GeneratorExit):
        # Client aborted the SSE request (stop button / navigation). The
        # async-for over streamer.stream() has already been unwound, which
        # closes the upstream LLM stream. Persist what streamed so far.
        # Guard on the joined text, not list truthiness: streamed_parts can
        # contain only empty strings (e.g. [""]) without being empty itself,
        # and both success paths above `.clear()` the buffer immediately
        # after persisting, so a late abort here reliably sees an empty
        # buffer instead of duplicating the already-persisted row.
        partial = "".join(streamed_parts)
        if session_factory is not None and partial:
            persist_stopped_detached(
                session_factory, ctx, chat_id=chat.id,
                user_message_id=user_message.id,
                content=partial, model_id=model.id,
            )
        raise
