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


class RegenerateRequest(BaseModel):
    """Optional body of POST /messages/{id}/regenerate."""

    model_id: UUID | None = None


class ChatCreate(BaseModel):
    workspace_id: UUID
    title: str | None = None


class ChatOut(BaseModel):
    id: UUID
    workspace_id: UUID
    title: str

    model_config = {"from_attributes": True}
