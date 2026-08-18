"""Reporting reads over chat history: answer quality, feedback rates, and the
admin feedback queue.

Split out of chat/service.py (Phase 2 item 2 of the 2026-08-17 architecture
review). These are pure read/aggregate queries with no overlap with message
persistence or streaming -- they only ever SELECT, and nothing else in the chat
module calls them -- so they were the cleanest seam to cut first.

Re-exported from chat.service so existing callers (api/routes/usage.py,
api/routes/admin_feedback.py) keep working unchanged.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from ragz.core.db import naive_utc
from ragz.core.errors import NotFoundError
from ragz.modules.auth.models import User
from ragz.modules.chat.models import Chat, Citation, Message, MessageFeedback
from ragz.modules.tenancy.context import TenantContext


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


@dataclass(frozen=True)
class FeedbackSummary:
    total_count: int
    down_count: int
    down_rate: float | None


async def feedback_summary(
    session: AsyncSession, ctx: TenantContext, *, days: int
) -> FeedbackSummary:
    cutoff = naive_utc() - timedelta(days=days)
    stmt = (
        select(MessageFeedback)
        .join(Message, Message.id == MessageFeedback.message_id)
        .join(Chat, Chat.id == Message.chat_id)
        .where(Chat.org_id == ctx.org_id, MessageFeedback.created_at >= cutoff)
    )
    rows = list((await session.execute(stmt)).scalars())
    total = len(rows)
    if total == 0:
        return FeedbackSummary(total_count=0, down_count=0, down_rate=None)
    down = sum(1 for r in rows if r.rating == "down")
    return FeedbackSummary(total_count=total, down_count=down, down_rate=down / total)


@dataclass(frozen=True)
class FeedbackQueueRow:
    message_id: UUID
    chat_id: UUID
    workspace_id: UUID
    question: str
    answer: str
    rating: str
    comment: str | None
    citations: list[Citation]
    created_at: datetime
    user_id: UUID | None
    user_email: str | None


async def list_feedback_queue(
    session: AsyncSession, ctx: TenantContext,
    *, rating: str | None = None, workspace_id: UUID | None = None,
    user_id: UUID | None = None, start: datetime | None = None, end: datetime | None = None,
    cursor: str | None = None, limit: int = 50,
) -> tuple[list[FeedbackQueueRow], str | None]:
    """Keyset-paginated, org-scoped (iron rule 1: every org-owned-table query
    goes through ctx.org_id). Mirrors list_audit_events's cursor shape
    ("{created_at.isoformat()}|{message_id}")."""
    stmt = (
        select(MessageFeedback, Message, Chat)
        .join(Message, Message.id == MessageFeedback.message_id)
        .join(Chat, Chat.id == Message.chat_id)
        .where(Chat.org_id == ctx.org_id)
        .order_by(MessageFeedback.created_at.desc(), MessageFeedback.message_id.desc())
    )
    if rating is not None:
        stmt = stmt.where(MessageFeedback.rating == rating)
    if workspace_id is not None:
        stmt = stmt.where(Chat.workspace_id == workspace_id)
    if user_id is not None:
        stmt = stmt.where(MessageFeedback.created_by == user_id)
    if start is not None:
        stmt = stmt.where(MessageFeedback.created_at >= start)
    if end is not None:
        stmt = stmt.where(MessageFeedback.created_at < end)
    if cursor:
        try:
            ts_raw, id_raw = cursor.split("|", 1)
            cursor_key = (datetime.fromisoformat(ts_raw), UUID(id_raw))
        except ValueError as exc:
            raise NotFoundError("invalid cursor") from exc
        stmt = stmt.where(
            tuple_(MessageFeedback.created_at, MessageFeedback.message_id) < cursor_key
        )
    rows = list((await session.execute(stmt.limit(limit + 1))).all())
    next_cursor = None
    if len(rows) > limit:
        rows = rows[:limit]
        last_fb, _, _ = rows[-1]
        next_cursor = f"{last_fb.created_at.isoformat()}|{last_fb.message_id}"

    message_ids = [m.id for _, m, _ in rows]
    parent_ids = [m.parent_message_id for _, m, _ in rows if m.parent_message_id is not None]
    parents: dict[UUID, Message] = {}
    if parent_ids:
        # Iron rule 1: re-filter on Chat.org_id at THIS query site too, even
        # though parent_ids are already derived from org-filtered rows above
        # and add_message's parent.chat_id != chat.id check independently
        # guarantees a parent shares its child's chat. Don't rely on either
        # invariant surviving a future refactor -- join through to Chat here.
        parents = {
            p.id: p
            for p in (
                await session.execute(
                    select(Message)
                    .join(Chat, Chat.id == Message.chat_id)
                    .where(Message.id.in_(parent_ids), Chat.org_id == ctx.org_id)
                )
            ).scalars()
        }
    citations_by_message: dict[UUID, list[Citation]] = defaultdict(list)
    if message_ids:
        # Same rationale: message_ids come from the org-filtered main query,
        # but this sub-query re-enforces org-scoping at its own query site
        # rather than inheriting safety from the caller.
        for c in (
            await session.execute(
                select(Citation)
                .join(Message, Message.id == Citation.message_id)
                .join(Chat, Chat.id == Message.chat_id)
                .where(Citation.message_id.in_(message_ids), Chat.org_id == ctx.org_id)
            )
        ).scalars():
            citations_by_message[c.message_id].append(c)

    # Batch-load the feedback authors' emails (iron rule 1: re-scope on
    # ctx.org_id at this query site too).
    author_ids = [fb.created_by for fb, _, _ in rows if fb.created_by is not None]
    authors: dict[UUID, str] = {}
    if author_ids:
        authors = {
            u.id: u.email
            for u in (
                await session.execute(
                    select(User).where(User.id.in_(author_ids), User.org_id == ctx.org_id)
                )
            ).scalars()
        }

    result = [
        FeedbackQueueRow(
            message_id=m.id, chat_id=chat.id, workspace_id=chat.workspace_id,
            question=(
                parents[m.parent_message_id].content
                if m.parent_message_id is not None and m.parent_message_id in parents
                else ""
            ),
            answer=m.content, rating=fb.rating, comment=fb.comment,
            citations=sorted(citations_by_message.get(m.id, []), key=lambda c: c.marker),
            created_at=fb.created_at,
            user_id=fb.created_by,
            user_email=authors.get(fb.created_by) if fb.created_by is not None else None,
        )
        for fb, m, chat in rows
    ]
    return result, next_cursor
