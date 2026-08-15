from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class GoldenQueryCreate(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    expected_document_ids: list[UUID] = Field(default_factory=list, max_length=500)


class GoldenQueryOut(BaseModel):
    id: UUID
    workspace_id: UUID
    question: str
    expected_document_ids: list[UUID]
    created_by: UUID
    created_at: datetime

    model_config = {"from_attributes": True}


class EvalRunOut(BaseModel):
    id: UUID
    workspace_id: UUID
    triggered_by: str
    query_count: int
    hit_rate: float | None
    citation_precision: float | None
    avg_faithfulness: float | None
    created_at: datetime

    model_config = {"from_attributes": True}
