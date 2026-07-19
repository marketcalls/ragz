from datetime import datetime
from uuid import UUID

from sqlalchemy import ForeignKey, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from raghub.core.db import Base, UUIDPk, naive_utc

DEFAULT_CHAT_TITLE = "New chat"


class Chat(UUIDPk, Base):
    __tablename__ = "chats"

    org_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"), index=True)
    workspace_id: Mapped[UUID] = mapped_column(ForeignKey("workspaces.id"), index=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(default=DEFAULT_CHAT_TITLE)
    updated_at: Mapped[datetime] = mapped_column(default=naive_utc, onupdate=naive_utc)


class Message(UUIDPk, Base):
    """One node of the conversation TREE (spec 2.1).

    An edit inserts a new user message sharing the edited message's parent
    (next sibling_index); a regenerate inserts a new assistant sibling under
    the same user message. Postgres treats NULLs as distinct, so the unique
    constraint only covers non-root siblings; the chat service enforces dense
    sibling_index for roots and strict role alternation.
    """

    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint("chat_id", "parent_message_id", "sibling_index",
                         name="uq_messages_sibling"),
    )

    chat_id: Mapped[UUID] = mapped_column(
        ForeignKey("chats.id", ondelete="CASCADE"), index=True
    )
    parent_message_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"), default=None, index=True
    )
    sibling_index: Mapped[int] = mapped_column(default=0)
    role: Mapped[str]  # "user" | "assistant"
    content: Mapped[str] = mapped_column(Text())
    model_id: Mapped[UUID | None] = mapped_column(default=None)
    prompt_tokens: Mapped[int | None] = mapped_column(default=None)
    completion_tokens: Mapped[int | None] = mapped_column(default=None)
    stopped: Mapped[bool] = mapped_column(default=False, server_default="false")
    no_answer: Mapped[bool] = mapped_column(default=False, server_default="false")


class Citation(UUIDPk, Base):
    __tablename__ = "citations"

    message_id: Mapped[UUID] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"), index=True
    )
    # Deliberately NOT an FK: document deletion must never touch chat history.
    document_id: Mapped[UUID]
    chunk_ref: Mapped[str]  # "{document_id}:{page}:{chunk_index}"
    page: Mapped[int]
    score: Mapped[float]
    marker: Mapped[int]  # the [n] number used in the answer text
    # Plan H (CHAT-4): section path + the document's version at citation time.
    section: Mapped[str | None] = mapped_column(Text(), default=None)
    version: Mapped[int] = mapped_column(default=1)
