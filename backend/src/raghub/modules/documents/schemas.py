from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class DocumentOut(BaseModel):
    id: UUID
    filename: str
    mime: str
    size_bytes: int
    status: str
    page_count: int | None
    error: str | None
    created_at: datetime
    pinned: bool
    acl_group_ids: list[UUID] | None = None

    model_config = {"from_attributes": True}


class DocumentPatch(BaseModel):
    pinned: bool


class AclUpdate(BaseModel):
    acl_group_ids: list[UUID] | None
