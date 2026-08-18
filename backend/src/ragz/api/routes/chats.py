from collections.abc import AsyncIterator
from typing import Annotated
from uuid import UUID

import httpx
import structlog
from fastapi import APIRouter, Depends, File, Request, Response, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ragz.api.deps import get_session
from ragz.core.config import Settings, get_settings
from ragz.core.errors import ConflictError, NotFoundError, PayloadTooLarge
from ragz.core.storage import build_storage
from ragz.modules.chat import service
from ragz.modules.chat.events import SSEEvent
from ragz.modules.chat.llm import LiteLLMStreamer, LLMCompleter, LLMStreamer
from ragz.modules.chat.models import Chat, ChatAttachment
from ragz.modules.chat.prompting import PromptSource
from ragz.modules.chat.schemas import (
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
from ragz.modules.chat.web import build_web_searcher
from ragz.modules.models import keys
from ragz.modules.models import service as models_service
from ragz.modules.models.models import Model
from ragz.modules.quotas import service as quota_service
from ragz.modules.tenancy import service as tenancy_service
from ragz.modules.tenancy.context import (
    TenantContext,
    get_tenant_context,
    rate_limit_user,
    require_action,
)
from ragz.modules.tenancy.models import Workspace
from ragz.modules.tenancy.views import WorkspaceView
from ragz.worker.tasks import enqueue_attachment_processing, enqueue_audit_message

router = APIRouter(tags=["chat"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
CtxDep = Annotated[TenantContext, Depends(get_tenant_context)]
# Per-user (not per-IP) limit on message sends: 30 per 60s (iron rule 4).
SendCtxDep = Annotated[TenantContext, Depends(rate_limit_user("chat_send", 30, 60))]
# RBAC-04/RBAC-03: chat send/create/regenerate require the chat.generate action
# (the granular successor to the legacy chat.use flag, retired from DEFAULT_USER_
# PERMISSIONS by the deny-by-default change). chat.generate is in the non-
# destructive default, so an ordinary member still chats; a custom role that
# omits it cannot. Added to the existing dependency list -- does not replace F's
# quota deps or G's rate limit above.
_ChatGenerateDep = Depends(require_action("chat.generate"))
# RBAC-03 Task 6: the remaining granular chat gates -- read/update/delete/
# feedback/attachments.create. Each is in the non-destructive default role's
# permission set, so an ordinary member is unaffected; a custom role that
# omits one of these is denied at the route boundary (dependencies=[...] on
# the decorator), matching _ChatGenerateDep's own pattern above.
_ChatReadDep = Depends(require_action("chat.read"))
_ChatUpdateDep = Depends(require_action("chat.update"))
_ChatDeleteDep = Depends(require_action("chat.delete"))
_ChatFeedbackDep = Depends(require_action("chat.feedback"))
_ChatAttachDep = Depends(require_action("chat.attachments.create"))

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
    "/chats", status_code=201, response_model=ChatOut, dependencies=[_ChatGenerateDep]
)
async def create_chat(body: ChatCreate, session: SessionDep, ctx: CtxDep) -> ChatOut:
    chat = await service.create_chat(
        session, ctx, workspace_id=body.workspace_id, title=body.title
    )
    if body.first_message is not None:
        # The opening turn is persisted with the chat, so it can no longer be
        # lost between "chat created" and "message sent". No reply is generated
        # here -- this route doesn't stream; the caller follows up with
        # POST /messages/{id}/answer.
        await service.add_user_message(session, ctx, chat, body.first_message)
    return _chat_out(chat)


@router.get("/chats", response_model=list[ChatOut], dependencies=[_ChatReadDep])
async def list_chats(
    session: SessionDep, ctx: CtxDep, workspace_id: UUID | None = None
) -> list[ChatOut]:
    return [
        _chat_out(c) for c in await service.list_chats(session, ctx, workspace_id=workspace_id)
    ]


@router.get("/chats/{chat_id}", response_model=ChatTreeOut, dependencies=[_ChatReadDep])
async def get_chat_tree(chat_id: UUID, session: SessionDep, ctx: CtxDep) -> ChatTreeOut:
    return await service.get_chat_tree(session, ctx, chat_id)


@router.patch("/chats/{chat_id}", response_model=ChatOut, dependencies=[_ChatUpdateDep])
async def rename_chat(
    chat_id: UUID, body: ChatPatch, session: SessionDep, ctx: CtxDep
) -> ChatOut:
    return _chat_out(await service.rename_chat(session, ctx, chat_id, body.title))


@router.delete("/chats/{chat_id}", status_code=204, dependencies=[_ChatDeleteDep])
async def delete_chat(chat_id: UUID, session: SessionDep, ctx: CtxDep) -> None:
    await service.delete_chat(session, ctx, chat_id)


@router.post("/chats/{chat_id}/messages", dependencies=[_ChatGenerateDep])
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
    # DOC-9 Task 5: resolve + route attachments BEFORE persisting the user
    # message -- same "fail fast" convention as the workspace/model
    # resolution and quota check above. Ownership is re-verified per
    # attachment (chat_id == chat.id) rather than trusted from create_
    # attachment's own check: that only proved the id belonged to THIS chat
    # at upload time, not that the caller didn't slip in an id from a
    # different chat in this request's body.
    attachment_sources: list[PromptSource] = []
    # DOC-9 Task 6: kind="image" attachments on a vision-capable model skip
    # Task 5's route_attachment (OCR extract_text + inline/retrieval routing)
    # entirely -- their raw bytes go to the model directly as an image block
    # instead (service.stream_reply fetches + encodes them). Every other
    # attachment (documents, and images on a non-vision model) is unaffected.
    image_attachments: list[ChatAttachment] = []
    # Transcript rendering (design 2026-08-15): every attachment on this turn
    # (image or document, inline-routed or retrieval-routed), so it can be
    # stamped with the user message's id once that message exists below.
    sent_attachments: list[ChatAttachment] = []
    if body.attachment_ids:
        for marker, attachment_id in enumerate(body.attachment_ids, start=1):
            attachment = await session.get(ChatAttachment, attachment_id)
            if attachment is None or attachment.chat_id != chat.id:
                raise NotFoundError("attachment not found in this chat")
            sent_attachments.append(attachment)
            if attachment.kind == "image" and model.supports_vision:
                image_attachments.append(attachment)
                continue
            source = await service.route_attachment(
                session, ctx.org_id, chat.id, attachment, marker, model.litellm_model_name,
            )
            if source is not None:
                attachment_sources.append(source)
    user_msg = await service.add_user_message(
        session, ctx, chat, body.content,
        parent_message_id=body.parent_message_id,
        explicit="parent_message_id" in body.model_fields_set,
    )
    if sent_attachments:
        await service.link_attachments_to_message(session, sent_attachments, user_msg.id)
    streamer = await _streamer(request, session, settings, ctx)
    completer: LLMCompleter | None = request.app.state.llm_completer
    if completer is None and isinstance(streamer, LiteLLMStreamer):
        completer = streamer  # same gateway client, non-streaming endpoint
    return _sse(service.stream_reply(
        session, ctx, chat=chat,
        workspace=WorkspaceView.of(workspace), user_message=user_msg, model=model,
        streamer=streamer, retriever=request.app.state.retriever,
        chunk_reader=request.app.state.chunk_reader, settings=settings,
        session_factory=request.app.state.session_factory, completer=completer,
        # DDG-01: DuckDuckGo is the DEFAULT, keyless web-search provider --
        # web search works out of the box with no superadmin secret setup.
        # Tavily stays available/optional: inject a TavilySearcher via
        # app.state.web_searcher (create_app's web_searcher= kwarg) to opt a
        # deployment into it instead.
        web_searcher=request.app.state.web_searcher
        or await build_web_searcher(session, settings),
        reasoning_effort=reasoning_effort, attachment_sources=attachment_sources,
        image_attachments=image_attachments,
        web_search_consented=body.web_search_consented,
        # RAGZ-PUB-08 residual: threads the shared Redis client through so
        # the persistent per-user/day web-search cap (settings.web_search_
        # daily_limit_per_user) survives across turns, not just this request.
        redis=request.app.state.redis,
    ))


@router.post("/messages/{message_id}/regenerate", dependencies=[_ChatGenerateDep])
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
        session, ctx, chat=chat,
        workspace=WorkspaceView.of(workspace), user_message=user_msg, model=model,
        streamer=streamer, retriever=request.app.state.retriever,
        chunk_reader=request.app.state.chunk_reader, settings=settings,
        session_factory=request.app.state.session_factory, completer=completer,
        # DDG-01: DuckDuckGo is the DEFAULT, keyless web-search provider --
        # web search works out of the box with no superadmin secret setup.
        # Tavily stays available/optional: inject a TavilySearcher via
        # app.state.web_searcher (create_app's web_searcher= kwarg) to opt a
        # deployment into it instead.
        web_searcher=request.app.state.web_searcher
        or await build_web_searcher(session, settings),
        reasoning_effort=reasoning_effort,
        web_search_consented=body.web_search_consented if body is not None else False,
        # RAGZ-PUB-08 residual: same persistent daily cap as send_message.
        redis=request.app.state.redis,
    ))


@router.post("/messages/{message_id}/answer", dependencies=[_ChatGenerateDep])
async def answer(
    message_id: UUID, request: Request,
    session: SessionDep, settings: SettingsDep, ctx: SendCtxDep,
    body: RegenerateRequest | None = None,
) -> StreamingResponse:
    """Generate the reply to an ALREADY-PERSISTED user message that has none.

    The companion to ChatCreate.first_message: the opening turn is persisted by
    POST /chats, and this streams its answer. It also makes the whole flow
    resumable -- a client that reloads mid-turn re-finds the unanswered message
    and calls this, instead of the message being stranded.

    Idempotency guard: refuses once an assistant reply exists, so a double call
    (or a reload racing the first stream) can't fork a second answer under the
    same user message. Re-answering an already-answered turn is `regenerate`.
    Body is RegenerateRequest -- identical option set (model/effort/consent).
    """
    chat, msg = await service.get_message(session, ctx, message_id)
    if msg.role != service.ROLE_USER:
        raise ConflictError("only a user message can be answered")
    messages = await service.list_messages(session, chat.id)
    if any(
        m.parent_message_id == msg.id and m.role == service.ROLE_ASSISTANT for m in messages
    ):
        raise ConflictError("this message already has an answer -- use regenerate")
    workspace, model = await _resolve_workspace_and_model(
        session, ctx, chat, body.model_id if body is not None else None
    )
    reasoning_effort = _resolve_reasoning_effort(
        model, body.reasoning_effort if body is not None else None
    )
    await quota_service.check_quota(
        session, request.app.state.redis, org_id=ctx.org_id, user_id=ctx.user_id
    )
    streamer = await _streamer(request, session, settings, ctx)
    completer: LLMCompleter | None = request.app.state.llm_completer
    if completer is None and isinstance(streamer, LiteLLMStreamer):
        completer = streamer  # same gateway client, non-streaming endpoint
    return _sse(service.stream_reply(
        session, ctx, chat=chat,
        workspace=WorkspaceView.of(workspace), user_message=msg, model=model,
        streamer=streamer, retriever=request.app.state.retriever,
        chunk_reader=request.app.state.chunk_reader, settings=settings,
        session_factory=request.app.state.session_factory, completer=completer,
        web_searcher=request.app.state.web_searcher
        or await build_web_searcher(session, settings),
        reasoning_effort=reasoning_effort,
        web_search_consented=body.web_search_consented if body is not None else False,
        redis=request.app.state.redis,
    ))


@router.put(
    "/messages/{message_id}/feedback", response_model=FeedbackOut,
    dependencies=[_ChatFeedbackDep],
)
async def set_feedback(
    message_id: UUID, body: MessageFeedbackIn, session: SessionDep, ctx: CtxDep,
) -> FeedbackOut:
    fb = await service.set_message_feedback(
        session, ctx, message_id, rating=body.rating, comment=body.comment
    )
    return FeedbackOut.model_validate(fb)


@router.delete(
    "/messages/{message_id}/feedback", status_code=204, dependencies=[_ChatFeedbackDep],
)
async def clear_feedback(message_id: UUID, session: SessionDep, ctx: CtxDep) -> None:
    await service.clear_message_feedback(session, ctx, message_id)


@router.post(
    "/chats/{chat_id}/attachments", status_code=201, response_model=AttachmentOut,
    dependencies=[_ChatAttachDep],
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


@router.get(
    "/chats/{chat_id}/attachments/{attachment_id}/content", dependencies=[_ChatReadDep],
)
async def get_attachment_content(
    chat_id: UUID, attachment_id: UUID, session: SessionDep, ctx: CtxDep
) -> Response:
    """Streams a chat attachment's bytes inline (image thumbnails / previews in
    the conversation). Chat-ownership scoped: get_attachment_for_chat ->
    get_chat enforces org_id + user_id, so no cross-user access; the attachment
    must also belong to THIS chat. Mirrors GET /documents/{id}/file -- the read
    path history otherwise exposes attachment METADATA only (no bytes)."""
    attachment = await service.get_attachment_for_chat(session, ctx, chat_id, attachment_id)
    storage = build_storage(get_settings())
    try:
        data = await storage.get(attachment.storage_key)
    except NotFoundError as exc:
        structlog.get_logger("ragz.chat").warning(
            "attachment_missing_in_storage",
            chat_id=str(chat_id), attachment_id=str(attachment_id),
            storage_key=attachment.storage_key,
        )
        raise NotFoundError("The attachment file is not available in storage.") from exc
    return Response(
        content=data,
        media_type=attachment.mime or "application/octet-stream",
        headers={"Content-Disposition": f'inline; filename="{attachment.filename}"'},
    )
