from uuid import UUID

from sqlalchemy import CheckConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ragz.core.db import Base, UUIDPk


class AuditEvent(UUIDPk, Base):
    __tablename__ = "audit_events"
    # RBAC-07: mirror the migration's ck_audit_events_result CHECK here so the
    # create_all()-built test schema enforces the same enum the migration adds
    # in production (follows the User/WorkspaceMember precedent).
    __table_args__ = (
        CheckConstraint("result IN ('success', 'denied')", name="ck_audit_events_result"),
    )

    org_id: Mapped[UUID | None] = mapped_column(index=True, default=None)
    actor_id: Mapped[UUID | None] = mapped_column(default=None)
    action: Mapped[str] = mapped_column(index=True)
    target_type: Mapped[str]
    target_id: Mapped[str]
    result: Mapped[str] = mapped_column(default="success")
    reason_code: Mapped[str | None] = mapped_column(default=None)
    request_id: Mapped[str | None] = mapped_column(default=None)
    source_ip: Mapped[str | None] = mapped_column(default=None)
    auth_method: Mapped[str | None] = mapped_column(default=None)
    credential_id: Mapped[str | None] = mapped_column(default=None)
