"""Post-hoc answer auditing: score a persisted assistant message for grounding
and completeness with the utility model.

Split out of chat/service.py (Phase 2 item 2 of the 2026-08-17 architecture
review). This runs AFTER a reply is persisted, on its own worker task -- it
shares no state with the streaming path beyond reading the rows that path
wrote, which is why it lifts out cleanly.

Not to be confused with modules/audit, the append-only event log. This is
answer-quality scoring; the scores it writes are what chat.analytics reports on.

Re-exported from chat.service; every existing caller keeps working unchanged.
"""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ragz.core.config import Settings, get_settings
from ragz.modules.chat.llm import LiteLLMStreamer
from ragz.modules.chat.models import Chat, Citation, Message
from ragz.modules.chat.prompting import PromptSource
from ragz.modules.chat.validation import build_auditor_messages, parse_auditor_scores
from ragz.modules.models.utility import get_utility_model
from ragz.modules.quotas import service as quota_service


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
        session, org_id=chat.org_id, user_id=chat.user_id, workspace_id=chat.workspace_id,
        model_id=utility_model.id,
        feature="validation", prompt_tokens=completion.usage.prompt_tokens,
        completion_tokens=completion.usage.completion_tokens,
    )
    await session.commit()
    return True