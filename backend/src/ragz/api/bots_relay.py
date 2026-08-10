"""The single bot->RAG seam (design doc §Components item 5). Lives in
ragz.api (not ragz.modules.bots) because it calls
ragz.api.routes.external._run_external_answer -- the import-linter layers
contract (ragz.api > ragz.modules > ragz.core) forbids modules importing
api, so this glue -- which is inherently api-layer (it needs the FastAPI
Request/app.state _run_external_answer itself depends on) -- sits beside
ragz/api/deps.py rather than under modules/bots/.

answer_for_integration adds NO retrieval/LLM logic of its own: it resolves
the integration's OWN user into a TenantContext narrowed to the
integration's OWN workspace_id (never the user's full membership set -- the
same key-narrowing hook api_key_context uses), maps the platform's
external_chat_id to a Ragz Chat (get-or-create via bots.service's
bot_conversations table), and returns _run_external_answer's answer text
UNCHANGED -- no re-formatting, no re-synthesis (iron rule 5)."""

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ragz.api.routes.external import _run_external_answer
from ragz.core.config import Settings
from ragz.core.errors import NotFoundError
from ragz.modules.auth.models import User
from ragz.modules.bots import service as bots_service
from ragz.modules.bots.models import BotIntegration
from ragz.modules.tenancy.context import build_verified_principal_context


async def answer_for_integration(
    request: Request, session: AsyncSession, settings: Settings, integration: BotIntegration,
    *, external_chat_id: str, text: str,
) -> str:
    user = (
        await session.execute(select(User).where(User.id == integration.user_id))
    ).scalar_one_or_none()
    if user is None or not user.active:
        raise NotFoundError("bot integration's user no longer active")
    # RBAC-02: revalidate the integration user's CURRENT workspace membership +
    # chat.generate (the granular successor to chat.use, per RBAC-04) on every
    # inbound message, never trusting the workspace stored on the integration at
    # creation. Raises AuthenticationError if revoked.
    ctx = await build_verified_principal_context(
        session, user, workspace_id=integration.workspace_id
    )
    existing_chat_id = await bots_service.get_mapped_chat_id(
        session, integration_id=integration.id, external_chat_id=external_chat_id
    )
    # _run_external_answer's audit call reads request.state.api_key_id,
    # stashed there by api_key_context (external.py) for its own callers.
    # There is no API key on the bot path, so we stash the analogous
    # per-caller identifier ourselves -- the integration id -- using the
    # same request.state scratch slot, so the audit trail still records
    # WHICH caller (this bot integration, not a raw API key) reached the
    # shared RAG path, without touching external.py's audit call at all.
    request.state.api_key_id = f"bot:{integration.id}"
    collected, chat = await _run_external_answer(
        request, session, settings, ctx, question=text, conversation_id=existing_chat_id,
    )
    if existing_chat_id is None:
        # get-or-create, not an atomic upsert: two inbound messages for the
        # same external_chat_id can both observe get_mapped_chat_id -> None,
        # each create their own Chat via _run_external_answer, and then race
        # to insert the (integration_id, external_chat_id) mapping row. The
        # loser's INSERT hits the unique constraint (bot_conversations
        # model's uq_bot_conversations_chat) -- caught here and re-read
        # rather than propagated, the same race-then-reread pattern
        # documents/folders.py's ensure_path uses for concurrent path
        # creation. The loser's own answer/chat are still valid and already
        # committed by _run_external_answer; only the mapping is discarded
        # in favor of the winner's, so the NEXT inbound message for this
        # external_chat_id lands on one consistent chat.
        try:
            await bots_service.save_chat_mapping(
                session, integration_id=integration.id,
                external_chat_id=external_chat_id, chat_id=chat.id,
            )
        except IntegrityError:
            await session.rollback()
            existing_chat_id = await bots_service.get_mapped_chat_id(
                session, integration_id=integration.id, external_chat_id=external_chat_id
            )
            assert existing_chat_id is not None  # a concurrent writer must have created it
    return collected.answer
