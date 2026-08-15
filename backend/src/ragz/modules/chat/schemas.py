from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class MessageSend(BaseModel):
    """Body of POST /chats/{id}/messages.

    parent_message_id semantics (spec 2.1 edit flow):
    - field ABSENT  -> append to the active leaf (newest-sibling path)
    - field null    -> new ROOT sibling (edit of a root user message)
    - field <uuid>  -> that message becomes the parent (edit-and-resend: pass
      the edited message's parent id)
    Presence is detected via model_fields_set.

    model_id: optional per-message model override (Plan D's top-bar selector);
    None/absent -> the workspace default model.
    """

    content: str = Field(min_length=1, max_length=32000)
    parent_message_id: UUID | None = None
    model_id: UUID | None = None
    reasoning_effort: Literal["off", "low", "medium", "high"] | None = None
    attachment_ids: list[UUID] | None = Field(default=None, max_length=50)
    # RAGZ-PUB-08 item 2: explicit per-turn user consent to let the agent run
    # an EXTERNAL web search (the redacted, user-question-derived query leaves
    # Ragz to a third-party provider). Fail-closed: absent/False => the planner
    # cannot perform any web_search this turn, even when the workspace has web
    # search enabled and a Tavily key stored.
    web_search_consented: bool = False


class RegenerateRequest(BaseModel):
    """Optional body of POST /messages/{id}/regenerate."""

    model_id: UUID | None = None
    reasoning_effort: Literal["off", "low", "medium", "high"] | None = None
    # RAGZ-PUB-08 item 2: same explicit external-web-search consent as
    # MessageSend; fail-closed default means a regenerate never issues a new
    # external search unless the caller re-consents.
    web_search_consented: bool = False


class ChatCreate(BaseModel):
    workspace_id: UUID
    title: str | None = Field(default=None, max_length=200)


class ChatOut(BaseModel):
    id: UUID
    workspace_id: UUID
    title: str
    created_at: datetime
    updated_at: datetime
    # Task 9 (spec §5): not a mapped ORM attribute name -- from_attributes
    # can't derive it, so every construction site passes it explicitly as
    # `has_summary=chat.summary is not None`.
    has_summary: bool

    model_config = {"from_attributes": True}


class ChatPatch(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class CitationOut(BaseModel):
    marker: int
    document_id: UUID | None
    chunk_ref: str
    page: int
    score: float
    section: str | None
    version: int
    url: str | None

    model_config = {"from_attributes": True}


class FeedbackOut(BaseModel):
    rating: str
    comment: str | None

    model_config = {"from_attributes": True}


class MessageFeedbackIn(BaseModel):
    rating: Literal["up", "down"]
    comment: str | None = Field(default=None, max_length=4000)


class MessageNode(BaseModel):
    id: UUID
    parent_message_id: UUID | None
    sibling_index: int
    role: str
    content: str
    model_id: UUID | None
    prompt_tokens: int | None
    completion_tokens: int | None
    created_at: datetime
    stopped: bool = False
    no_answer: bool = False
    grounding: str = "documents"
    grounding_score: float | None = None
    completeness_score: float | None = None
    validation_failed: bool = False
    citations: list[CitationOut]
    feedback: FeedbackOut | None
    children: list["MessageNode"]


MessageNode.model_rebuild()


class ChatTreeOut(BaseModel):
    id: UUID
    workspace_id: UUID
    title: str
    has_summary: bool
    messages: list[MessageNode]


class AttachmentOut(BaseModel):
    id: UUID
    kind: str
    filename: str
    mime: str
    status: str

    model_config = {"from_attributes": True}
