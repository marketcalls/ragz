from typing import Literal
from uuid import UUID

from pydantic import BaseModel, EmailStr


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
    password: str


class UserOut(BaseModel):
    id: UUID
    email: EmailStr
    role: Literal["admin", "user", "superadmin"]
    active: bool

    model_config = {"from_attributes": True}


class UserPatch(BaseModel):
    active: bool | None = None
    role: Literal["admin", "user"] | None = None
