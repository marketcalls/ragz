from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

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
