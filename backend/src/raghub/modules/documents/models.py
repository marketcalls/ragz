from datetime import datetime
from uuid import UUID

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
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
    # None = unrestricted (every pre-Phase-2 document); a list = only members of
    # those groups (and admins) may retrieve or open it (RBAC-5).
    acl_group_ids: Mapped[list[UUID] | None] = mapped_column(
        ARRAY(PG_UUID(as_uuid=True)), default=None
    )
    # Plan H (DOC-5): version lineage
    version: Mapped[int] = mapped_column(default=1)
    lineage_id: Mapped[UUID] = mapped_column(index=True)  # v1 row's own id; no FK (self-ref churn)
    supersedes_document_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("documents.id", ondelete="SET NULL"), default=None
    )
    is_current: Mapped[bool] = mapped_column(default=False, index=True)
    approved: Mapped[bool] = mapped_column(default=False)
    vectors_present: Mapped[bool] = mapped_column(default=False)
    # Plan H (DOC-6): admin-defined metadata field values, keyed by
    # MetadataField.name. None = never set (distinct from {} = cleared).
    meta: Mapped[dict[str, str] | None] = mapped_column(JSONB, default=None)


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


class MetadataField(UUIDPk, Base):
    """Admin-defined metadata field on a workspace (DOC-6): text|date|select.
    Preset-seeded lazily by modules/documents/metadata.py::list_fields."""

    __tablename__ = "metadata_fields"
    __table_args__ = (
        UniqueConstraint("workspace_id", "name", name="uq_metadata_fields_ws_name"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(40))  # machine key: ^[a-z0-9_]{1,40}$
    label: Mapped[str]
    field_type: Mapped[str]  # text|date|select
    options: Mapped[list[str] | None] = mapped_column(ARRAY(String), default=None)  # select only
    position: Mapped[int] = mapped_column(default=0)
