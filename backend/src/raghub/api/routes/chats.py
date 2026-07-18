from collections.abc import AsyncIterator
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from raghub.api.deps import get_session
from raghub.core.config import Settings, get_settings
from raghub.core.errors import ConflictError
from raghub.modules.chat import service
from raghub.modules.chat.events import SSEEvent
from raghub.modules.chat.llm import LiteLLMStreamer, LLMStreamer
from raghub.modules.chat.models import Chat
from raghub.modules.chat.schemas import ChatCreate, ChatOut, MessageSend, RegenerateRequest
from raghub.modules.models import service as models_service
from raghub.modules.models.models import Model
from raghub.modules.tenancy import service as tenancy_service
from raghub.modules.tenancy.context import TenantContext, get_tenant_context, rate_limit_user

router = APIRouter(tags=["chat"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
CtxDep = Annotated[TenantContext, Depends(get_tenant_context)]
# Per-user (not per-IP) limit on message sends: 30 per 60s (iron rule 4).
SendCtxDep = Annotated[TenantContext, Depends(rate_limit_user("chat_send", 30, 60))]

_SSE_HEADERS = {"Cache-Control": "no-store", "X-Accel-Buffering": "no"}


def _streamer(request: Request, settings: Settings) -> LLMStreamer:
    injected: LLMStreamer | None = request.app.state.llm_streamer
    if injected is not None:
        return injected
    return LiteLLMStreamer(
        base_url=settings.litellm_url, master_key=settings.litellm_master_key
    )


async def _encoded(events: AsyncIterator[SSEEvent]) -> AsyncIterator[str]:
    async for event in events:
        yield event.encode()


def _sse(events: AsyncIterator[SSEEvent]) -> StreamingResponse:
    return StreamingResponse(
        _encoded(events), media_type="text/event-stream", headers=_SSE_HEADERS
    )


async def _resolve_model(
    session: AsyncSession, ctx: TenantContext, chat: Chat,
    requested_model_id: UUID | None,
) -> Model:
    """Explicit body model_id -> workspace default -> typed error (404/409 as
    problem+json, BEFORE any SSE bytes are sent)."""
    workspace = await tenancy_service.get_workspace(session, ctx, chat.workspace_id)
    return await models_service.resolve_model(
        session, requested_model_id=requested_model_id,
        default_model_id=workspace.default_model_id,
    )


@router.post("/chats", status_code=201, response_model=ChatOut)
async def create_chat(body: ChatCreate, session: SessionDep, ctx: CtxDep) -> ChatOut:
    chat = await service.create_chat(
        session, ctx, workspace_id=body.workspace_id, title=body.title
    )
    return ChatOut.model_validate(chat)


@router.post("/chats/{chat_id}/messages")
async def send_message(
    chat_id: UUID, body: MessageSend, request: Request,
    session: SessionDep, settings: SettingsDep, ctx: SendCtxDep,
) -> StreamingResponse:
    chat = await service.get_chat(session, ctx, chat_id)
    model = await _resolve_model(session, ctx, chat, body.model_id)  # fail fast
    messages = await service.list_messages(session, chat.id)
    parent = service.resolve_parent(
        messages, body.parent_message_id,
        explicit="parent_message_id" in body.model_fields_set,
    )
    user_msg = await service.add_message(
        session, ctx, chat, role=service.ROLE_USER, content=body.content, parent=parent
    )
    return _sse(service.stream_reply(
        session, ctx, chat=chat, user_message=user_msg, model=model,
        streamer=_streamer(request, settings),
        retriever=request.app.state.retriever, settings=settings,
    ))


@router.post("/messages/{message_id}/regenerate")
async def regenerate(
    message_id: UUID, request: Request,
    session: SessionDep, settings: SettingsDep, ctx: SendCtxDep,
    body: RegenerateRequest | None = None,
) -> StreamingResponse:
    chat, msg = await service.get_message(session, ctx, message_id)
    if msg.role != service.ROLE_ASSISTANT or msg.parent_message_id is None:
        raise ConflictError("only assistant messages can be regenerated")
    model = await _resolve_model(
        session, ctx, chat, body.model_id if body is not None else None
    )
    messages = await service.list_messages(session, chat.id)
    user_msg = next(m for m in messages if m.id == msg.parent_message_id)
    return _sse(service.stream_reply(
        session, ctx, chat=chat, user_message=user_msg, model=model,
        streamer=_streamer(request, settings),
        retriever=request.app.state.retriever, settings=settings,
    ))
