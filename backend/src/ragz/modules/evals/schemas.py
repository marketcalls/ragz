from datetime import datetime
from typing import Literal
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


class AnswerComparisonRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    model_id: UUID | None = None


class ComparisonSourceOut(BaseModel):
    marker: int
    document_id: UUID
    filename: str
    page: int
    chunk_index: int
    score: float
    snippet: str
    section: str | None
    version: int


class ComparisonVariantOut(BaseModel):
    mode: Literal["single", "multi"]
    answer: str
    sources: list[ComparisonSourceOut]
    citation_markers: list[int]
    no_answer: bool
    query_count: int
    retrieval_ms: float
    generation_ms: float
    total_ms: float
    prompt_tokens: int
    completion_tokens: int


class AnswerComparisonOut(BaseModel):
    variants: list[ComparisonVariantOut] = Field(min_length=2, max_length=2)
