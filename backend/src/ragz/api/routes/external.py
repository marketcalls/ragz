"""External API surface: the API-key auth dependency (Task 3) plus
`POST /external/v1/chat` (Task 4) and the OpenAI-compatible
`/external/v1/openai/...` routes (sub-project #3). `api_key_context`
resolves a raw key via the single `resolve_api_key` verification path (iron
rule 3), loads+checks the owning user, and narrows the resulting
`TenantContext` to the key's single workspace_id (the key-narrowing hook in
`build_context_for_user`).

Every external answer route funnels through `_run_external_answer`, which
adds NO parallel RAG logic: it mirrors `chats.py::send_message`'s assembly
(same `_resolve_workspace_and_model`, `_streamer`, `request.app.state
.retriever`/`.chunk_reader` wiring) and collects `chat.service.stream_reply`'s
SSE events into one JSON answer via `chat.service.collect_reply` instead of
streaming them -- `external_chat` and the OpenAI `chat/completions` route
both call it and map its `CollectedAnswer` into their own response shape."""

import time
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ragz.api.deps import get_session
from ragz.api.routes import chats
from ragz.core.config import Settings, get_settings
from ragz.core.errors import AuthenticationError, BadRequestError, NotFoundError
from ragz.core.ratelimit import check_rate_limit
from ragz.modules.audit.service import record_audit
from ragz.modules.auth.api_keys_service import resolve_api_key
from ragz.modules.auth.models import User
from ragz.modules.chat import service
from ragz.modules.chat.llm import LiteLLMStreamer, LLMCompleter
from ragz.modules.chat.models import Chat
from ragz.modules.chat.schemas import CitationOut
from ragz.modules.models import service as models_service
from ragz.modules.quotas import service as quota_service
from ragz.modules.tenancy import service as tenancy_service
from ragz.modules.tenancy.context import TenantContext, build_verified_principal_context

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
    # Stashed for external_chat's audit event (design spec, Observability):
    # ApiKeyDep's return type is TenantContext, which has no room for the
    # key id, and every other caller of build_context_for_user (the regular
    # JWT path) has no key at all -- request.state is per-request scratch
    # space that doesn't touch that shared return type. Only the key's id
    # (a UUID), never the raw key.
    request.state.api_key_id = principal.key_id
    # RBAC-02: revalidate CURRENT membership + chat.use on every request rather
    # than trusting the workspace the key captured at issuance.
    return await build_verified_principal_context(
        session, user, workspace_id=principal.workspace_id
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


async def _run_external_answer(
    request: Request, session: AsyncSession, settings: Settings, ctx: TenantContext,
    *, question: str, conversation_id: UUID | None,
) -> tuple[service.CollectedAnswer, Chat]:
    """The shared body of every external answer route (Task 4 / sub-project
    #3): workspace -> conversation -> model resolution -> quota -> persist
    user turn -> collect_reply(stream_reply) -> audit. Pulled out of
    `external_chat` verbatim (pure refactor, no behavior change) so the
    OpenAI-compatible `chat/completions` route can reuse the exact same RAG
    path instead of growing a second one."""
    workspace_id = next(iter(ctx.workspace_ids))
    chat = await _get_or_create_conversation(session, ctx, workspace_id, conversation_id)
    workspace, model = await chats._resolve_workspace_and_model(session, ctx, chat, None)
    # Fail fast (same convention as send_message) -- BEFORE persisting the
    # user message: an external caller past quota gets a typed 429, not a
    # persisted turn with no answer.
    await quota_service.check_quota(
        session, request.app.state.redis, org_id=ctx.org_id, user_id=ctx.user_id
    )
    user_message = await service.add_user_message(session, ctx, chat, question)
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
    # Design spec, Observability: every external call gets an audit event
    # (only reached once collect_reply returns without raising, i.e. after a
    # successful answer -- an `error` SSE frame becomes an UpstreamError
    # inside collect_reply and skips this entirely, matching "audit AFTER a
    # successful answer"). record_audit's target_id has no separate
    # metadata field, so the key id (never the raw key) rides along in
    # target_id as "<workspace_id>:<key_id>".
    await record_audit(
        session, org_id=ctx.org_id, actor_id=ctx.user_id, action="external.chat",
        target_type="workspace", target_id=f"{workspace_id}:{request.state.api_key_id}",
    )
    await session.commit()
    return collected, chat


@router.post(
    "/chat", response_model=ExternalChatResponse,
    dependencies=[Depends(_rate_limit_external_chat)],
)
async def external_chat(
    body: ExternalChatRequest, request: Request,
    session: SessionDep, settings: SettingsDep, ctx: ApiKeyDep,
) -> ExternalChatResponse:
    collected, chat = await _run_external_answer(
        request, session, settings, ctx,
        question=body.question, conversation_id=body.conversation_id,
    )
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


class OpenAIMessage(BaseModel):
    role: str
    content: str


class OpenAIChatCompletionsRequest(BaseModel):
    """OpenAI Chat Completions request shape (subset). `extra="allow"` so
    unknown fields real OpenAI SDKs send (temperature, top_p, tools, ...)
    don't 422 -- they're accepted and ignored, since the workspace's
    configured model/retrieval settings are authoritative here, not the
    client's."""

    model_config = ConfigDict(extra="allow")

    model: str | None = None
    messages: list[OpenAIMessage]
    stream: bool = False


class OpenAIChoiceMessage(BaseModel):
    role: str
    content: str


class OpenAIChoice(BaseModel):
    index: int
    message: OpenAIChoiceMessage
    finish_reason: str


class OpenAIUsage(BaseModel):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class OpenAIExtra(BaseModel):
    citations: list[CitationOut]
    grounding: str
    no_answer: bool
    conversation_id: str


class OpenAIChatCompletionsResponse(BaseModel):
    id: str
    object: str = "chat.completion"
    created: int
    model: str
    choices: list[OpenAIChoice]
    usage: OpenAIUsage
    x_ragz: OpenAIExtra


class OpenAIModel(BaseModel):
    id: str
    object: str = "model"
    owned_by: str = "ragz"


class OpenAIModelsResponse(BaseModel):
    object: str = "list"
    data: list[OpenAIModel]


def _last_user_message(messages: list[OpenAIMessage]) -> str:
    for message in reversed(messages):
        if message.role == "user":
            return message.content
    raise BadRequestError("messages must include at least one role=\"user\" entry")


@router.post(
    "/openai/chat/completions", response_model=OpenAIChatCompletionsResponse,
    dependencies=[Depends(_rate_limit_external_chat)],
)
async def openai_chat_completions(
    body: OpenAIChatCompletionsRequest, request: Request,
    session: SessionDep, settings: SettingsDep, ctx: ApiKeyDep,
) -> OpenAIChatCompletionsResponse:
    """OpenAI-compatible `chat/completions` (sub-project #3, non-streaming
    only in v1). Reuses `_run_external_answer` -- the SAME RAG path as
    `external_chat` -- with `question` = the last role="user" message and a
    fresh conversation each call (v1: the client-sent history is not
    replayed as grounding; the workspace conversation is the memory)."""
    if body.stream:
        raise BadRequestError("streaming is not supported by this endpoint")
    question = _last_user_message(body.messages)
    collected, chat = await _run_external_answer(
        request, session, settings, ctx, question=question, conversation_id=None,
    )
    return OpenAIChatCompletionsResponse(
        id=f"chatcmpl-{uuid4().hex}",
        created=int(time.time()),
        model=body.model or "ragz",
        choices=[
            OpenAIChoice(
                index=0,
                message=OpenAIChoiceMessage(role="assistant", content=collected.answer),
                finish_reason="stop",
            )
        ],
        usage=OpenAIUsage(prompt_tokens=0, completion_tokens=0, total_tokens=0),
        x_ragz=OpenAIExtra(
            citations=[
                CitationOut(
                    marker=c.marker,
                    document_id=UUID(c.document_id) if c.document_id else None,
                    chunk_ref=c.chunk_ref, page=c.page, score=c.score,
                    section=c.section, version=c.version, url=c.url,
                )
                for c in collected.citations
            ],
            grounding=collected.grounding,
            no_answer=collected.no_answer,
            conversation_id=str(chat.id),
        ),
    )


@router.get("/openai/models", response_model=OpenAIModelsResponse)
async def openai_models(
    session: SessionDep, ctx: ApiKeyDep,
) -> OpenAIModelsResponse:
    """OpenAI-compatible `models` list (sub-project #3): the key's single
    workspace, listing its one configured chat model. Resolves the model the
    same way `_resolve_workspace_and_model` does (workspace default ->
    typed error) but without needing a persisted `Chat` -- there is no chat
    to attach one to for a pure model-listing call."""
    workspace_id = next(iter(ctx.workspace_ids))
    workspace = await tenancy_service.get_workspace(session, ctx, workspace_id)
    model = await models_service.resolve_model(
        session, requested_model_id=None, default_model_id=workspace.default_model_id,
    )
    return OpenAIModelsResponse(data=[OpenAIModel(id=model.litellm_model_name)])
