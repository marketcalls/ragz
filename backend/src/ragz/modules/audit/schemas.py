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
    result: str
    reason_code: str | None
    request_id: str | None
    source_ip: str | None
    auth_method: str | None
    credential_id: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class AuditPageOut(BaseModel):
    events: list[AuditEventOut]
    next_cursor: str | None
