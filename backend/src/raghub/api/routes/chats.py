from collections.abc import AsyncIterator
from typing import Annotated
from uuid import UUID

import httpx
import structlog
from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from raghub.api.deps import get_session
from raghub.core.config import Settings, get_settings
from raghub.core.errors import ConflictError, PayloadTooLarge
from raghub.modules.chat import service
from raghub.modules.chat.events import SSEEvent
from raghub.modules.chat.llm import LiteLLMStreamer, LLMCompleter, LLMStreamer
from raghub.modules.chat.models import Chat
from raghub.modules.chat.schemas import (
    AttachmentOut,
    ChatCreate,
    ChatOut,
    ChatPatch,
    ChatTreeOut,
    FeedbackOut,
    MessageFeedbackIn,
    MessageSend,
    RegenerateRequest,
)
from raghub.modules.chat.web import TavilySearcher
from raghub.modules.models import keys
from raghub.modules.models import service as models_service
from raghub.modules.models.models import Model
from raghub.modules.quotas import service as quota_service
from raghub.modules.tenancy import service as tenancy_service
from raghub.modules.tenancy.context import (
    TenantContext,
    get_tenant_context,
    rate_limit_user,
    require_permission,
)
from raghub.modules.tenancy.models import Workspace
from raghub.worker.tasks import enqueue_attachment_processing, enqueue_audit_message

router = APIRouter(tags=["chat"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
CtxDep = Annotated[TenantContext, Depends(get_tenant_context)]
# Per-user (not per-IP) limit on message sends: 30 per 60s (iron rule 4).
SendCtxDep = Annotated[TenantContext, Depends(rate_limit_user("chat_send", 30, 60))]
# Task 13 (RBAC-2, H-C12): ADDED to the existing dependency list on create/send/
# regenerate -- does not replace F's quota deps or G's rate limit above.
_ChatUseDep = Depends(require_permission("chat.use"))

_SSE_HEADERS = {"Cache-Control": "no-store", "X-Accel-Buffering": "no"}


async def _streamer(
    request: Request, session: AsyncSession, settings: Settings, ctx: TenantContext
) -> LLMStreamer:
    injected: LLMStreamer | None = request.app.state.llm_streamer
    if injected is not None:
        return injected
    status = await quota_service.get_usage_status(
        session, request.app.state.redis, org_id=ctx.org_id, user_id=ctx.user_id
    )
    vkey = await keys.get_or_create_user_virtual_key(
        session, settings, user_id=ctx.user_id, monthly_tokens=status.allocated_tokens,
        transport=request.app.state.litellm_transport,
    )
    return LiteLLMStreamer(
        base_url=settings.litellm_url, master_key=vkey or settings.litellm_master_key,
        transport=request.app.state.litellm_transport,
        limits=httpx.Limits(
            max_connections=settings.httpx_max_connections,
            max_keepalive_connections=settings.httpx_max_keepalive,
        ),
    )


async def _encoded(events: AsyncIterator[SSEEvent]) -> AsyncIterator[str]:
    async for event in events:
        if (
            event.event == "done"
            and event.data.get("grounding") == "documents"
            # Deviation from the Task 3 brief's literal snippet: also gate on
            # no_answer here, not just in audit_message. The decline-policy
            # no-answer path yields grounding="documents" too (nothing else
            # distinguishes it in the done frame), so skipping it only inside
            # the Celery task would still enqueue a task that always no-ops -
            # wasted broker traffic for a turn we already know carries
            # nothing to audit.
            and not event.data.get("no_answer")
        ):
            # Phase 3 Auditor (§3): fire-and-forget, never awaited, never
            # blocks the stream - a Celery enqueue failure (broker down) is
            # swallowed the same way a missed audit already is (worker side).
            try:
                enqueue_audit_message(UUID(str(event.data["message_id"])))
            except Exception:
                structlog.get_logger().warning("audit_enqueue_failed", exc_info=True)
        yield event.encode()


def _sse(events: AsyncIterator[SSEEvent]) -> StreamingResponse:
    return StreamingResponse(
        _encoded(events), media_type="text/event-stream", headers=_SSE_HEADERS
    )


async def _resolve_workspace_and_model(
    session: AsyncSession, ctx: TenantContext, chat: Chat,
    requested_model_id: UUID | None,
) -> tuple[Workspace, Model]:
    """Explicit body model_id -> workspace default -> typed error (404/409 as
    problem+json, BEFORE any SSE bytes are sent). The workspace is returned too:
    stream_reply needs its retrieval settings and prompt override."""
    workspace = await tenancy_service.get_workspace(session, ctx, chat.workspace_id)
    model = await models_service.resolve_model(
        session, requested_model_id=requested_model_id,
        default_model_id=workspace.default_model_id,
    )
    return workspace, model


def _resolve_reasoning_effort(model: Model, requested: str | None) -> str | None:
    """None/"off" always means "don't ask for reasoning" — never a conflict,
    even on a model with supports_reasoning=False. Any real tier
    (low/medium/high) on an unsupported model is rejected fast, before any
    SSE bytes are sent, same as the model_id resolution above it."""
    if requested is None or requested == "off":
        return None
    if not model.supports_reasoning:
        raise ConflictError(f"model does not support reasoning effort: {model.display_name}")
    return requested


def _chat_out(chat: Chat) -> ChatOut:
    # Explicit construction, not ChatOut.model_validate(chat): has_summary
    # (Task 9, spec §5) isn't a mapped ORM attribute name, so from_attributes
    # has nothing to read it from -- it's computed here instead.
    return ChatOut(
        id=chat.id, workspace_id=chat.workspace_id, title=chat.title,
        created_at=chat.created_at, updated_at=chat.updated_at,
        has_summary=chat.summary is not None,
    )


@router.post(
    "/chats", status_code=201, response_model=ChatOut, dependencies=[_ChatUseDep]
)
async def create_chat(body: ChatCreate, session: SessionDep, ctx: CtxDep) -> ChatOut:
    chat = await service.create_chat(
        session, ctx, workspace_id=body.workspace_id, title=body.title
    )
    return _chat_out(chat)


@router.get("/chats", response_model=list[ChatOut])
async def list_chats(
    session: SessionDep, ctx: CtxDep, workspace_id: UUID | None = None
) -> list[ChatOut]:
    return [
        _chat_out(c) for c in await service.list_chats(session, ctx, workspace_id=workspace_id)
    ]


@router.get("/chats/{chat_id}", response_model=ChatTreeOut)
async def get_chat_tree(chat_id: UUID, session: SessionDep, ctx: CtxDep) -> ChatTreeOut:
    return await service.get_chat_tree(session, ctx, chat_id)


@router.patch("/chats/{chat_id}", response_model=ChatOut)
async def rename_chat(
    chat_id: UUID, body: ChatPatch, session: SessionDep, ctx: CtxDep
) -> ChatOut:
    return _chat_out(await service.rename_chat(session, ctx, chat_id, body.title))


@router.delete("/chats/{chat_id}", status_code=204)
async def delete_chat(chat_id: UUID, session: SessionDep, ctx: CtxDep) -> None:
    await service.delete_chat(session, ctx, chat_id)


@router.post("/chats/{chat_id}/messages", dependencies=[_ChatUseDep])
async def send_message(
    chat_id: UUID, body: MessageSend, request: Request,
    session: SessionDep, settings: SettingsDep, ctx: SendCtxDep,
) -> StreamingResponse:
    chat = await service.get_chat(session, ctx, chat_id)
    workspace, model = await _resolve_workspace_and_model(
        session, ctx, chat, body.model_id
    )  # fail fast
    reasoning_effort = _resolve_reasoning_effort(model, body.reasoning_effort)
    await quota_service.check_quota(
        session, request.app.state.redis, org_id=ctx.org_id, user_id=ctx.user_id
    )
    messages = await service.list_messages(session, chat.id)
    parent = service.resolve_parent(
        messages, body.parent_message_id,
        explicit="parent_message_id" in body.model_fields_set,
    )
    user_msg = await service.add_message(
        session, ctx, chat, role=service.ROLE_USER, content=body.content, parent=parent
    )
    streamer = await _streamer(request, session, settings, ctx)
    completer: LLMCompleter | None = request.app.state.llm_completer
    if completer is None and isinstance(streamer, LiteLLMStreamer):
        completer = streamer  # same gateway client, non-streaming endpoint
    return _sse(service.stream_reply(
        session, ctx, chat=chat, workspace=workspace, user_message=user_msg, model=model,
        streamer=streamer, retriever=request.app.state.retriever,
        chunk_reader=request.app.state.chunk_reader, settings=settings,
        session_factory=request.app.state.session_factory, completer=completer,
        web_searcher=request.app.state.web_searcher or TavilySearcher(settings=settings),
        reasoning_effort=reasoning_effort,
    ))


@router.post("/messages/{message_id}/regenerate", dependencies=[_ChatUseDep])
async def regenerate(
    message_id: UUID, request: Request,
    session: SessionDep, settings: SettingsDep, ctx: SendCtxDep,
    body: RegenerateRequest | None = None,
) -> StreamingResponse:
    chat, msg = await service.get_message(session, ctx, message_id)
    if msg.role != service.ROLE_ASSISTANT or msg.parent_message_id is None:
        raise ConflictError("only assistant messages can be regenerated")
    workspace, model = await _resolve_workspace_and_model(
        session, ctx, chat, body.model_id if body is not None else None
    )
    reasoning_effort = _resolve_reasoning_effort(
        model, body.reasoning_effort if body is not None else None
    )
    await quota_service.check_quota(
        session, request.app.state.redis, org_id=ctx.org_id, user_id=ctx.user_id
    )
    messages = await service.list_messages(session, chat.id)
    user_msg = next(m for m in messages if m.id == msg.parent_message_id)
    streamer = await _streamer(request, session, settings, ctx)
    completer: LLMCompleter | None = request.app.state.llm_completer
    if completer is None and isinstance(streamer, LiteLLMStreamer):
        completer = streamer  # same gateway client, non-streaming endpoint
    return _sse(service.stream_reply(
        session, ctx, chat=chat, workspace=workspace, user_message=user_msg, model=model,
        streamer=streamer, retriever=request.app.state.retriever,
        chunk_reader=request.app.state.chunk_reader, settings=settings,
        session_factory=request.app.state.session_factory, completer=completer,
        web_searcher=request.app.state.web_searcher or TavilySearcher(settings=settings),
        reasoning_effort=reasoning_effort,
    ))


@router.put("/messages/{message_id}/feedback", response_model=FeedbackOut)
async def set_feedback(
    message_id: UUID, body: MessageFeedbackIn, session: SessionDep, ctx: CtxDep,
) -> FeedbackOut:
    fb = await service.set_message_feedback(
        session, ctx, message_id, rating=body.rating, comment=body.comment
    )
    return FeedbackOut.model_validate(fb)


@router.delete("/messages/{message_id}/feedback", status_code=204)
async def clear_feedback(message_id: UUID, session: SessionDep, ctx: CtxDep) -> None:
    await service.clear_message_feedback(session, ctx, message_id)


@router.post(
    "/chats/{chat_id}/attachments", status_code=201, response_model=AttachmentOut,
)
async def upload_attachment(
    chat_id: UUID, session: SessionDep, settings: SettingsDep, ctx: CtxDep,
    request: Request, file: Annotated[UploadFile, File()],
) -> AttachmentOut:
    max_bytes = settings.interactive_upload_mb * 1024 * 1024
    if content_length := request.headers.get("content-length"):
        try:
            if int(content_length) > max_bytes + 4096:
                raise PayloadTooLarge(
                    f"attachment exceeds {settings.interactive_upload_mb} MB limit"
                )
        except ValueError:
            pass
    buf = bytearray()
    while chunk := await file.read(1024 * 1024):
        buf.extend(chunk)
        if len(buf) > max_bytes:
            raise PayloadTooLarge(
                f"attachment exceeds {settings.interactive_upload_mb} MB limit"
            )
    attachment = await service.create_attachment(
        session, ctx, chat_id,
        filename=file.filename or "attachment.bin",
        mime=file.content_type or "application/octet-stream",
        data=bytes(buf),
    )
    enqueue_attachment_processing(attachment.id)
    return AttachmentOut.model_validate(attachment)
