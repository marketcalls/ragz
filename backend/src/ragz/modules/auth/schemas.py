from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class AccessTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"  # noqa: S105 (not a secret, just the auth scheme name)


class InvitationCreate(BaseModel):
    email: EmailStr
    role: Literal["admin", "user"] = "user"


class InvitationOut(BaseModel):
    invite_token: str


class InvitationAccept(BaseModel):
    token: str
    password: str = Field(min_length=12)


class UserOut(BaseModel):
    id: UUID
    email: EmailStr
    role: Literal["admin", "user", "superadmin"]
    active: bool
    custom_role_id: UUID | None = None

    model_config = {"from_attributes": True}


class UserPatch(BaseModel):
    active: bool | None = None
    role: Literal["admin", "user"] | None = None


class ApiKeyCreate(BaseModel):
    name: str
    user_id: UUID
    workspace_id: UUID
    expires_at: datetime | None = None


class ApiKeyOut(BaseModel):  # masked: NO key/hash (iron rule 3)
    id: UUID
    name: str
    prefix: str
    org_id: UUID
    user_id: UUID
    workspace_id: UUID
    created_by: UUID
    expires_at: datetime | None
    last_used_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class ApiKeyCreatedOut(ApiKeyOut):
    api_key: str  # the raw key -- present ONLY on the create response
