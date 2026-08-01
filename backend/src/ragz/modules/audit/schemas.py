from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class AuditEventOut(BaseModel):
    id: UUID
    org_id: UUID | None
    actor_id: UUID | None
    action: str
    target_type: str
    target_id: str
    created_at: datetime

    model_config = {"from_attributes": True}


class AuditPageOut(BaseModel):
    events: list[AuditEventOut]
    next_cursor: str | None
