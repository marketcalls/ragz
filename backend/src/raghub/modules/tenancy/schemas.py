from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class WorkspaceCreate(BaseModel):
    name: str


class WorkspaceOut(BaseModel):
    id: UUID
    name: str
    embedding_model: str
    min_score: float
    default_model_id: UUID | None
    top_k: int
    rerank_enabled: bool
    system_prompt_override: str | None
    fallback_policy: str
    web_search_enabled: bool

    model_config = {"from_attributes": True}


class WorkspacePatch(BaseModel):
    default_model_id: UUID | None = None
    top_k: int | None = Field(default=None, ge=1, le=50)
    min_score: float | None = Field(default=None, ge=0.0, le=1.0)
    rerank_enabled: bool | None = None
    system_prompt_override: str | None = Field(default=None, max_length=8000)
    fallback_policy: Literal["general_knowledge", "decline"] | None = None
    web_search_enabled: bool | None = None


class MemberAdd(BaseModel):
    user_id: UUID
    role: str = "member"


class GroupCreate(BaseModel):
    name: str


class GroupOut(BaseModel):
    id: UUID
    name: str
    member_ids: list[UUID]


class RoleTemplateCreate(BaseModel):
    name: str
    description: str = ""
    permissions: list[str]


class RoleTemplatePatch(BaseModel):
    name: str | None = None
    description: str | None = None
    permissions: list[str] | None = None


class RoleTemplateOut(BaseModel):
    id: UUID
    name: str
    description: str
    permissions: list[str]

    model_config = {"from_attributes": True}


class CustomRoleAssign(BaseModel):
    role_template_id: UUID | None
