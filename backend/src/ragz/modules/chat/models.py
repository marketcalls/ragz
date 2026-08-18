from datetime import datetime
from uuid import UUID

from sqlalchemy import ForeignKey, ForeignKeyConstraint, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ragz.core.db import Base, UUIDPk, naive_utc

DEFAULT_CHAT_TITLE = "New chat"


class Chat(UUIDPk, Base):
    __tablename__ = "chats"
    __table_args__ = (
        # Same-tenant composite FKs (e4f7c1a83b26): a plain FK on workspace_id
        # proves the workspace EXISTS, not that it belongs to this chat's org.
        ForeignKeyConstraint(
            ["workspace_id", "org_id"], ["workspaces.id", "workspaces.org_id"],
            name="fk_chats_workspace_id_org",
        ),
        ForeignKeyConstraint(
            ["user_id", "org_id"], ["users.id", "users.org_id"],
            name="fk_chats_user_id_org",
        ),
    )

    org_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"), index=True)
    workspace_id: Mapped[UUID] = mapped_column(ForeignKey("workspaces.id"), index=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(default=DEFAULT_CHAT_TITLE)
    updated_at: Mapped[datetime] = mapped_column(default=naive_utc, onupdate=naive_utc)
    # Plan K §5: rolling-summary memory. summary_upto_message_id is the
    # newest message already folded IN (an ancestor of summary_upto is
    # covered by the summary text; forking above it invalidates the summary
    # — see prompting.py/service.py in Task 9).
    summary: Mapped[str | None] = mapped_column(Text(), default=None)
    # use_alter=True: messages.chat_id already FKs to chats, so this reverse
    # FK creates a two-table cycle; without use_alter, SQLAlchemy's metadata
    # create_all/drop_all (used by the test DB fixture) can't topologically
    # sort CREATE/DROP TABLE and raises CircularDependencyError.
    summary_upto_message_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("messages.id", ondelete="SET NULL", use_alter=True,
                   name="fk_chats_summary_upto_message"),
        default=None,
    )


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
    # Phase 3 Plan I (design D3): "documents" | "general" - set to "general" ONLY
    # on general-knowledge fallback answers (RAG-miss + permissive policy).
    grounding: Mapped[str] = mapped_column(default="documents", server_default="documents")
    # Phase 3 Plan J (§3): Auditor scores, 0.0-1.0. None = never audited
    # (no utility model designated, or this message isn't grounding="documents").
    grounding_score: Mapped[float | None] = mapped_column(default=None)
    completeness_score: Mapped[float | None] = mapped_column(default=None)
    # Phase 3 Plan J (design D4/§3): the SECOND Gatekeeper attempt was
    # streamed (win or lose - see validation.synthesize_with_gatekeeper).
    validation_failed: Mapped[bool] = mapped_column(default=False, server_default="false")
    # In-chat generative UI (design 2026-08-15, §2/§4): the assistant
    # message's validated (blocks.py::validate_blocks) block array, stored as
    # plain JSON-able dicts -- null when the visualize step never ran
    # (workspace.generative_ui_enabled off, or no completer) or emitted
    # nothing (best-effort: junk/no-op model output degrades to null, same
    # as "the step never ran").
    blocks_json: Mapped[list[object] | None] = mapped_column(JSONB, default=None)


class Citation(UUIDPk, Base):
    __tablename__ = "citations"

    message_id: Mapped[UUID] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"), index=True
    )
    # Deliberately NOT an FK: document deletion must never touch chat history.
    # Phase 3 Plan I Task 11 (D7): nullable -- a web citation has no document row.
    document_id: Mapped[UUID | None]
    chunk_ref: Mapped[str]  # "{document_id}:{page}:{chunk_index}" | "web:{url}"
    page: Mapped[int]
    score: Mapped[float]
    marker: Mapped[int]  # the [n] number used in the answer text
    # Plan H (CHAT-4): section path + the document's version at citation time.
    section: Mapped[str | None] = mapped_column(Text(), default=None)
    version: Mapped[int] = mapped_column(default=1)
    # Phase 3 Plan I Task 11 (D7): set only for web citations; None for document ones.
    url: Mapped[str | None] = mapped_column(Text(), default=None)


class ChatAttachment(UUIDPk, Base):
    __tablename__ = "chat_attachments"

    chat_id: Mapped[UUID] = mapped_column(
        ForeignKey("chats.id", ondelete="CASCADE"), index=True
    )
    # Transcript rendering: which user Message this attachment was actually
    # sent on, set once at send time (chats.py::send_message, AFTER the
    # message is persisted). NULL until then -- an uploaded-but-not-yet-sent
    # attachment (or one that's stale and swept by the TTL job) has no
    # message to hang off. SET NULL on message delete: the attachment row
    # itself is chat-scoped, not message-owned (matches the existing 24h TTL
    # sweep, which keys off created_at, not the message's lifecycle).
    message_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("messages.id", ondelete="SET NULL"), default=None, index=True
    )
    kind: Mapped[str]  # "document" | "image"
    filename: Mapped[str]
    mime: Mapped[str]
    storage_key: Mapped[str]
    # "queued" | "processing" | "ready" | "failed"
    status: Mapped[str] = mapped_column(default="queued")
    extracted_text: Mapped[str | None] = mapped_column(Text(), default=None)
    # Set at message-send time once a routing decision is made for this
    # attachment in a given send — "inline" | "retrieval" | None (not yet used).
    routed_to: Mapped[str | None] = mapped_column(default=None)


class MessageFeedback(Base):
    """One row per message (1:1), same PK-is-the-FK pattern as
    OrgQuota/UserQuota. Chats are single-user, so there is no per-voter
    fan-out to model."""

    __tablename__ = "message_feedback"

    message_id: Mapped[UUID] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"), primary_key=True
    )
    rating: Mapped[str]  # "up" | "down"
    comment: Mapped[str | None] = mapped_column(Text(), default=None)
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(default=naive_utc)
    updated_at: Mapped[datetime] = mapped_column(default=naive_utc, onupdate=naive_utc)
