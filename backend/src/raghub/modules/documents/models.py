from datetime import datetime
from uuid import UUID

from sqlalchemy import ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from raghub.core.db import Base, UUIDPk, naive_utc


class Document(UUIDPk, Base):
    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint("workspace_id", "content_hash", name="uq_documents_workspace_hash"),
    )

    org_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"), index=True)
    workspace_id: Mapped[UUID] = mapped_column(ForeignKey("workspaces.id"), index=True)
    filename: Mapped[str]
    mime: Mapped[str]
    size_bytes: Mapped[int]
    content_hash: Mapped[str]
    status: Mapped[str] = mapped_column(default="queued")  # queued|processing|indexed|failed
    error: Mapped[str | None] = mapped_column(default=None)
    storage_key: Mapped[str]
    page_count: Mapped[int | None] = mapped_column(default=None)
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    updated_at: Mapped[datetime] = mapped_column(default=naive_utc, onupdate=naive_utc)
    pinned: Mapped[bool] = mapped_column(default=False, index=True)


class IngestJob(UUIDPk, Base):
    __tablename__ = "ingest_jobs"

    document_id: Mapped[UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    stage: Mapped[str]  # parse|chunk|embed|upsert
    progress: Mapped[float] = mapped_column(default=0.0)
    error: Mapped[str | None] = mapped_column(default=None)
    started_at: Mapped[datetime | None] = mapped_column(default=None)
    finished_at: Mapped[datetime | None] = mapped_column(default=None)
