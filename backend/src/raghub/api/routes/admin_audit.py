from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from raghub.api.deps import get_session
from raghub.modules.audit.schemas import AuditEventOut, AuditPageOut
from raghub.modules.audit.service import list_audit_events
from raghub.modules.tenancy.context import TenantContext, require_role

router = APIRouter(prefix="/admin", tags=["admin-audit"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
SuperDep = Annotated[TenantContext, Depends(require_role("superadmin"))]


@router.get("/audit", response_model=AuditPageOut)
async def get_audit(
    session: SessionDep,
    ctx: SuperDep,
    action: str | None = None,
    actor_id: UUID | None = None,
    org_id: UUID | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> AuditPageOut:
    events, next_cursor = await list_audit_events(
        session, action=action, actor_id=actor_id, org_id=org_id,
        date_from=date_from, date_to=date_to, cursor=cursor, limit=limit,
    )
    return AuditPageOut(
        events=[AuditEventOut.model_validate(e) for e in events], next_cursor=next_cursor
    )
