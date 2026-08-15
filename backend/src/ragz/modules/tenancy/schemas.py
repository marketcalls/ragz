from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class WorkspaceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class WorkspaceOut(BaseModel):
    id: UUID
    name: str
    embedding_model_id: UUID
    min_score: float
    default_model_id: UUID | None
    top_k: int
    rerank_enabled: bool
    system_prompt_override: str | None
    fallback_policy: str
    web_search_enabled: bool
    strict_mode: bool
    enrichment_enabled: bool
    chunk_method: str

    model_config = {"from_attributes": True}


class EmbeddingModelPatch(BaseModel):
    embedding_model_id: UUID


class WorkspacePatch(BaseModel):
    default_model_id: UUID | None = None
    top_k: int | None = Field(default=None, ge=1, le=50)
    min_score: float | None = Field(default=None, ge=0.0, le=1.0)
    rerank_enabled: bool | None = None
    system_prompt_override: str | None = Field(default=None, max_length=8000)
    fallback_policy: Literal["general_knowledge", "decline"] | None = None
    web_search_enabled: bool | None = None
    strict_mode: bool | None = None
    enrichment_enabled: bool | None = None
    chunk_method: Literal["heading", "fixed", "page", "table_qa"] | None = None


class ReembedRequest(BaseModel):
    new_embedding_model_id: UUID


class ReembedJobOut(BaseModel):
    id: UUID
    workspace_id: UUID
    old_embedding_model_id: UUID
    new_embedding_model_id: UUID
    documents_total: int
    documents_done: int
    error: str | None
    finished_at: str | None  # ISO 8601, None while running

    model_config = {"from_attributes": True}


WorkspaceRole = Literal["owner", "manager", "contributor", "viewer"]


class MemberAdd(BaseModel):
    user_id: UUID
    role: WorkspaceRole = "contributor"


class MemberOut(BaseModel):
    user_id: UUID
    role: WorkspaceRole

    model_config = {"from_attributes": True}


class MemberRolePatch(BaseModel):
    role: WorkspaceRole


class GroupCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class GroupOut(BaseModel):
    id: UUID
    name: str
    member_ids: list[UUID]


class RoleTemplateCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)
    permissions: list[str] = Field(max_length=500)


class RoleTemplatePatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    permissions: list[str] | None = Field(default=None, max_length=500)


class RoleTemplateOut(BaseModel):
    id: UUID
    name: str
    description: str
    permissions: list[str]
    status: str
    version: int

    model_config = {"from_attributes": True}


class CustomRoleAssign(BaseModel):
    role_template_id: UUID | None
