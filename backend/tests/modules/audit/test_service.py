import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ragz.modules.audit.models import AuditEvent
from ragz.modules.audit.service import record_audit


async def test_record_audit_defaults_result_success_and_captures_request_id(
    session: AsyncSession,
) -> None:
    structlog.contextvars.bind_contextvars(request_id="req-123")
    try:
        await record_audit(
            session, org_id=None, actor_id=None, action="test.action",
            target_type="t", target_id="1",
        )
        await session.commit()
    finally:
        structlog.contextvars.clear_contextvars()
    row = (
        await session.execute(select(AuditEvent).where(AuditEvent.action == "test.action"))
    ).scalar_one()
    assert row.result == "success"
    assert row.request_id == "req-123"


async def test_record_audit_denial_event(session: AsyncSession) -> None:
    await record_audit(
        session, org_id=None, actor_id=None, action="documents.upload",
        target_type="route", target_id="/api/v1/workspaces/x/documents",
        result="denied", reason_code="missing_action",
    )
    await session.commit()
    row = (
        await session.execute(
            select(AuditEvent).where(AuditEvent.action == "documents.upload")
        )
    ).scalar_one()
    assert row.result == "denied" and row.reason_code == "missing_action"
