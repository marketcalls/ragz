from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field


class LoginRequest(BaseModel):
    # RAGZ-PUB-03: /auth/login is public + rate-limited but still cheap to
    # hit repeatedly -- bound both fields so an oversized body can't force
    # unnecessary work (email-validator parsing, Argon2id verification over
    # an attacker-chosen huge password) before the rate limiter even helps.
    # 320 mirrors RFC 5321's max mailbox length; 4096 is generous headroom
    # over any real password while still bounding Argon2's per-request cost.
    email: EmailStr = Field(max_length=320)
    password: str = Field(max_length=4096)


class AccessTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"  # noqa: S105 (not a secret, just the auth scheme name)


class InvitationCreate(BaseModel):
    email: EmailStr = Field(max_length=320)
    role: Literal["admin", "user"] = "user"


class InvitationOut(BaseModel):
    invite_token: str


class InvitationAccept(BaseModel):
    # RAGZ-PUB-03: /auth/invitations/accept is public + rate-limited. The
    # raw token is `secrets.token_urlsafe(32)` (service.py, ~43 chars) --
    # 512 is generous headroom over that; password max mirrors LoginRequest.
    token: str = Field(max_length=512)
    password: str = Field(min_length=12, max_length=4096)


class ForgotPasswordRequest(BaseModel):
    # RAGZ-PUB-06: /auth/forgot-password is public + rate-limited. Bounded
    # like LoginRequest.email so an oversized body can't force unnecessary
    # work before the rate limiter helps.
    email: EmailStr = Field(max_length=320)


class ResetPasswordRequest(BaseModel):
    # Same token-length rationale as InvitationAccept.token; password rule
    # mirrors InvitationAccept.password verbatim.
    token: str = Field(max_length=512)
    new_password: str = Field(min_length=12, max_length=4096)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(max_length=4096)
    new_password: str = Field(min_length=12, max_length=4096)


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
    name: str = Field(min_length=1, max_length=200)
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
