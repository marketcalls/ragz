from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from raghub.api.deps import get_session
from raghub.modules.chat import service
from raghub.modules.chat.schemas import CitationOut
from raghub.modules.tenancy.context import TenantContext, require_role

router = APIRouter(prefix="/admin", tags=["admin-feedback"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
AdminDep = Annotated[TenantContext, Depends(require_role("admin"))]


class FeedbackQueueItemOut(BaseModel):
    message_id: UUID
    chat_id: UUID
    workspace_id: UUID
    question: str
    answer: str
    rating: str
    comment: str | None
    citations: list[CitationOut]
    created_at: datetime


class FeedbackQueuePageOut(BaseModel):
    items: list[FeedbackQueueItemOut]
    next_cursor: str | None


@router.get("/feedback", response_model=FeedbackQueuePageOut)
async def get_feedback_queue(
    session: SessionDep,
    ctx: AdminDep,
    rating: str = "down",
    workspace_id: UUID | None = None,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> FeedbackQueuePageOut:
    rows, next_cursor = await service.list_feedback_queue(
        session, ctx, rating=rating, workspace_id=workspace_id, cursor=cursor, limit=limit,
    )
    return FeedbackQueuePageOut(
        items=[
            FeedbackQueueItemOut(
                message_id=r.message_id, chat_id=r.chat_id, workspace_id=r.workspace_id,
                question=r.question, answer=r.answer, rating=r.rating, comment=r.comment,
                citations=[CitationOut.model_validate(c) for c in r.citations],
                created_at=r.created_at,
            )
            for r in rows
        ],
        next_cursor=next_cursor,
    )
