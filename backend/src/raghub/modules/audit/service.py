from datetime import datetime
from uuid import UUID

from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from raghub.core.errors import NotFoundError
from raghub.modules.audit.models import AuditEvent


async def record_audit(
    session: AsyncSession,
    *,
    org_id: UUID | None,
    actor_id: UUID | None,
    action: str,
    target_type: str,
    target_id: str,
) -> None:
    session.add(
        AuditEvent(
            org_id=org_id, actor_id=actor_id, action=action,
            target_type=target_type, target_id=target_id,
        )
    )


async def list_audit_events(
    session: AsyncSession,
    *,
    action: str | None = None,
    actor_id: UUID | None = None,
    org_id: UUID | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    cursor: str | None = None,
    limit: int = 50,
) -> tuple[list[AuditEvent], str | None]:
    """Keyset-paginated read path for SUP-5. Cursor is "{created_at}|{id}" of
    the last row seen; malformed cursors raise NotFoundError (a stale bookmark,
    not a server fault)."""
    stmt = select(AuditEvent).order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
    if action:
        stmt = stmt.where(AuditEvent.action.startswith(action))
    if actor_id is not None:
        stmt = stmt.where(AuditEvent.actor_id == actor_id)
    if org_id is not None:
        stmt = stmt.where(AuditEvent.org_id == org_id)
    if date_from is not None:
        stmt = stmt.where(AuditEvent.created_at >= date_from)
    if date_to is not None:
        stmt = stmt.where(AuditEvent.created_at <= date_to)
    if cursor:
        try:
            ts_raw, id_raw = cursor.split("|", 1)
            cursor_key = (datetime.fromisoformat(ts_raw), UUID(id_raw))
        except ValueError as exc:
            raise NotFoundError("invalid cursor") from exc
        stmt = stmt.where(tuple_(AuditEvent.created_at, AuditEvent.id) < cursor_key)
    rows = list((await session.execute(stmt.limit(limit + 1))).scalars())
    next_cursor = None
    if len(rows) > limit:
        rows = rows[:limit]
        last = rows[-1]
        next_cursor = f"{last.created_at.isoformat()}|{last.id}"
    return rows, next_cursor
