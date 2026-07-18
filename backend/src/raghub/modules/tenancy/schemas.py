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

    model_config = {"from_attributes": True}


class WorkspacePatch(BaseModel):
    default_model_id: UUID | None = None
    top_k: int | None = Field(default=None, ge=1, le=50)
    min_score: float | None = Field(default=None, ge=0.0, le=1.0)
    rerank_enabled: bool | None = None
    system_prompt_override: str | None = Field(default=None, max_length=8000)


class MemberAdd(BaseModel):
    user_id: UUID
    role: str = "member"
