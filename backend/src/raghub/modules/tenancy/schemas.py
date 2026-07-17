from uuid import UUID

from pydantic import BaseModel


class WorkspaceCreate(BaseModel):
    name: str


class WorkspaceOut(BaseModel):
    id: UUID
    name: str
    embedding_model: str
    min_score: float

    model_config = {"from_attributes": True}


class MemberAdd(BaseModel):
    user_id: UUID
    role: str = "member"
