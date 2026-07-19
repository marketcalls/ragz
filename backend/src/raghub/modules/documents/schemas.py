from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


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
    # Invariant: acl_group_ids is None === unrestricted (every workspace
    # member may read it). [] is never a valid payload value — see AclUpdate.
    acl_group_ids: list[UUID] | None = None
    version: int
    lineage_id: UUID
    is_current: bool
    approved: bool
    supersedes_document_id: UUID | None
    meta: dict[str, str] | None = None

    model_config = {"from_attributes": True}


class DocumentPatch(BaseModel):
    pinned: bool


class ApprovedPatch(BaseModel):
    approved: bool


class AclUpdate(BaseModel):
    """PUT /documents/{id}/acl body.

    Invariant: `null` clears the restriction (unrestricted — every workspace
    member may read the document); a non-null list is the exact set of groups
    allowed to read it. `[]` is INVALID and rejected with 422 — an empty list
    would otherwise decode ambiguously (get_document_checked treats it as
    "restricted to no groups", i.e. admins-only, while the wire payload for
    "unrestricted" is `null`). To clear a restriction, send `acl_group_ids:
    null`, never `[]`.
    """

    acl_group_ids: list[UUID] | None = Field(min_length=1)


class MetadataFieldCreate(BaseModel):
    """POST /workspaces/{id}/metadata-fields body (DOC-6)."""

    name: str = Field(pattern=r"^[a-z0-9_]{1,40}$")
    label: str
    field_type: str
    options: list[str] | None = None


class MetadataFieldOut(BaseModel):
    id: UUID
    name: str
    label: str
    field_type: str
    options: list[str] | None
    position: int

    model_config = {"from_attributes": True}


class MetadataValuesIn(BaseModel):
    """PUT /documents/{id}/metadata body — full replacement of doc.meta."""

    values: dict[str, str]
