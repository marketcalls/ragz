from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ragz.api.deps import get_session
from ragz.modules.audit.schemas import AuditEventOut, AuditPageOut
from ragz.modules.audit.service import list_audit_events
from ragz.modules.tenancy.context import TenantContext, require_action

router = APIRouter(prefix="/admin", tags=["admin-audit"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
# RBAC-05: audit.read is an explicit, org-scoped grant independent of
# admin/IAM duties (NIST AC-5) -- not require_role("superadmin") anymore.
AuditReadDep = Annotated[TenantContext, Depends(require_action("audit.read"))]


@router.get("/audit", response_model=AuditPageOut)
async def get_audit(
    session: SessionDep,
    ctx: AuditReadDep,
    action: str | None = None,
    actor_id: UUID | None = None,
    org_id: UUID | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> AuditPageOut:
    # A non-superadmin Audit Reader can only ever see their OWN org's events --
    # the org_id query param is ignored/overridden for them, never trusted.
    # Superadmin keeps the existing platform-wide view (documented, deferred
    # separation -- see RBAC-05 design decision).
    effective_org_id = org_id if ctx.role == "superadmin" else ctx.org_id
    events, next_cursor = await list_audit_events(
        session, action=action, actor_id=actor_id, org_id=effective_org_id,
        date_from=date_from, date_to=date_to, cursor=cursor, limit=limit,
    )
    return AuditPageOut(
        events=[AuditEventOut.model_validate(e) for e in events], next_cursor=next_cursor
    )
