from uuid import UUID

from pydantic import BaseModel, Field


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(default=8, ge=1, le=50)


class ChunkOut(BaseModel):
    document_id: UUID
    page: int
    chunk_index: int
    text: str
    score: float


class SearchResponse(BaseModel):
    no_answer: bool
    chunks: list[ChunkOut]
