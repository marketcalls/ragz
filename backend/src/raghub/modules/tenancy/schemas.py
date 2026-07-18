from uuid import UUID

from pydantic import BaseModel


class WorkspaceCreate(BaseModel):
    name: str


class WorkspaceOut(BaseModel):
    id: UUID
    name: str
    embedding_model: str
    min_score: float
    default_model_id: UUID | None

    model_config = {"from_attributes": True}


class WorkspacePatch(BaseModel):
    default_model_id: UUID | None = None


class MemberAdd(BaseModel):
    user_id: UUID
    role: str = "member"
