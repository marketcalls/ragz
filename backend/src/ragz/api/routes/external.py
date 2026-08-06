"""External API surface: the API-key auth dependency (Task 3) plus
`POST /external/v1/chat` (Task 4). `api_key_context` resolves a raw key via
the single `resolve_api_key` verification path (iron rule 3), loads+checks
the owning user, and narrows the resulting `TenantContext` to the key's
single workspace_id (the key-narrowing hook in `build_context_for_user`).

The chat route adds NO parallel RAG logic: it mirrors
`chats.py::send_message`'s assembly (same `_resolve_workspace_and_model`,
`_streamer`, `request.app.state.retriever`/`.chunk_reader` wiring) and
collects `chat.service.stream_reply`'s SSE events into one JSON answer via
`chat.service.collect_reply` instead of streaming them."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ragz.api.deps import get_session
from ragz.api.routes import chats
from ragz.core.config import Settings, get_settings
from ragz.core.errors import AuthenticationError, NotFoundError
from ragz.core.ratelimit import check_rate_limit
from ragz.modules.auth.api_keys_service import resolve_api_key
from ragz.modules.auth.models import User
from ragz.modules.chat import service
from ragz.modules.chat.llm import LiteLLMStreamer, LLMCompleter
from ragz.modules.chat.models import Chat
from ragz.modules.chat.schemas import CitationOut
from ragz.modules.quotas import service as quota_service
from ragz.modules.tenancy.context import TenantContext, build_context_for_user

SessionDep = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


def _extract_key(request: Request) -> str | None:
    auth = request.headers.get("authorization")
    if auth and auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.headers.get("x-api-key")


async def api_key_context(
    request: Request, session: SessionDep, settings: SettingsDep
) -> TenantContext:
    raw = _extract_key(request)
    if not raw:
        raise AuthenticationError("missing API key")
    principal = await resolve_api_key(session, settings, raw_key=raw)
    if principal is None:
        raise AuthenticationError("invalid API key")
    user = (
        await session.execute(select(User).where(User.id == principal.user_id))
    ).scalar_one_or_none()
    if user is None or not user.active:
        raise AuthenticationError("invalid API key")
    return await build_context_for_user(
        session, user, workspace_ids=frozenset({principal.workspace_id})
    )


ApiKeyDep = Annotated[TenantContext, Depends(api_key_context)]

router = APIRouter(tags=["external"])


async def _rate_limit_external_chat(request: Request, ctx: ApiKeyDep) -> None:
    """Per-key-user limiter (iron rule 4: rate limiting on external routes).
    Can't reuse `tenancy/context.py::rate_limit_user` directly -- its guard
    closure re-derives the context via `get_tenant_context` (JWT bearer
    decode), which would try to decode the raw API key as a JWT and always
    401 before the real ApiKeyDep-based auth even runs. This guard sources
    ctx the same way the route itself does (ApiKeyDep) and calls the SAME
    single rate-limiting primitive (`check_rate_limit`) `rate_limit_user`
    uses, with the same `rl:{scope}:user:{user_id}` key shape."""
    await check_rate_limit(
        request.app.state.redis, f"rl:external_chat:user:{ctx.user_id}", 60, 60
    )


class ExternalChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=32000)
    conversation_id: UUID | None = None


class ExternalChatResponse(BaseModel):
    answer: str
    citations: list[CitationOut]
    no_answer: bool
    grounding: str
    conversation_id: UUID


async def _get_or_create_conversation(
    session: AsyncSession, ctx: TenantContext, workspace_id: UUID, conversation_id: UUID | None,
) -> Chat:
    """create-or-get for the key's user: a caller-supplied `conversation_id`
    must resolve to a chat owned by this user (service.get_chat already
    scopes by ctx.org_id + ctx.user_id) AND belonging to the key's OWN
    workspace -- checked explicitly here rather than relying on
    `_resolve_workspace_and_model`'s downstream `get_workspace` call, since
    that call only rejects a workspace outside ctx.workspace_ids for
    role=="user" (admins/superadmins pass any org workspace there by
    design). Without this check, an admin- or superadmin-owned key could be
    used to reach a DIFFERENT workspace than the one it was issued for by
    simply passing that workspace's own chat id -- defeating the whole point
    of narrowing a key to one workspace."""
    if conversation_id is not None:
        chat = await service.get_chat(session, ctx, conversation_id)
        if chat.workspace_id != workspace_id:
            raise NotFoundError("conversation not found in this workspace")
        return chat
    return await service.create_chat(session, ctx, workspace_id=workspace_id)


@router.post(
    "/chat", response_model=ExternalChatResponse,
    dependencies=[Depends(_rate_limit_external_chat)],
)
async def external_chat(
    body: ExternalChatRequest, request: Request,
    session: SessionDep, settings: SettingsDep, ctx: ApiKeyDep,
) -> ExternalChatResponse:
    workspace_id = next(iter(ctx.workspace_ids))
    chat = await _get_or_create_conversation(session, ctx, workspace_id, body.conversation_id)
    workspace, model = await chats._resolve_workspace_and_model(session, ctx, chat, None)
    # Fail fast (same convention as send_message) -- BEFORE persisting the
    # user message: an external caller past quota gets a typed 429, not a
    # persisted turn with no answer.
    await quota_service.check_quota(
        session, request.app.state.redis, org_id=ctx.org_id, user_id=ctx.user_id
    )
    user_message = await service.add_user_message(session, ctx, chat, body.question)
    streamer = await chats._streamer(request, session, settings, ctx)
    completer: LLMCompleter | None = request.app.state.llm_completer
    if completer is None and isinstance(streamer, LiteLLMStreamer):
        completer = streamer  # same gateway client, non-streaming endpoint
    collected = await service.collect_reply(service.stream_reply(
        session, ctx, chat=chat, workspace=workspace, user_message=user_message, model=model,
        streamer=streamer, retriever=request.app.state.retriever,
        chunk_reader=request.app.state.chunk_reader, settings=settings,
        session_factory=request.app.state.session_factory, completer=completer,
    ))
    return ExternalChatResponse(
        answer=collected.answer,
        citations=[
            CitationOut(
                marker=c.marker,
                document_id=UUID(c.document_id) if c.document_id else None,
                chunk_ref=c.chunk_ref, page=c.page, score=c.score,
                section=c.section, version=c.version, url=c.url,
            )
            for c in collected.citations
        ],
        no_answer=collected.no_answer,
        grounding=collected.grounding,
        conversation_id=chat.id,
    )
