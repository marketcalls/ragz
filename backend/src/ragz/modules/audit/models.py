from uuid import UUID

from sqlalchemy.orm import Mapped, mapped_column

from ragz.core.db import Base, UUIDPk


class AuditEvent(UUIDPk, Base):
    __tablename__ = "audit_events"

    org_id: Mapped[UUID | None] = mapped_column(index=True, default=None)
    actor_id: Mapped[UUID | None] = mapped_column(default=None)
    action: Mapped[str] = mapped_column(index=True)
    target_type: Mapped[str]
    target_id: Mapped[str]
