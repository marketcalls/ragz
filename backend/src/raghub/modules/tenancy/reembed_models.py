"""ReembedJob: workspace-scoped progress tracking for a DOC-10 embedding-model
switch (POST /workspaces/{id}/reembed, Task 7). Mirrors documents/models.py's
IngestJob shape, scoped to a workspace instead of a document -- one job
covers every document in the workspace, so progress is a document count
pair (done/total) rather than a single float."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from raghub.core.db import Base, UUIDPk


class ReembedJob(UUIDPk, Base):
    __tablename__ = "reembed_jobs"

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    old_embedding_model_id: Mapped[UUID] = mapped_column(ForeignKey("models.id"))
    new_embedding_model_id: Mapped[UUID] = mapped_column(ForeignKey("models.id"))
    documents_total: Mapped[int] = mapped_column(default=0)
    documents_done: Mapped[int] = mapped_column(default=0)
    error: Mapped[str | None] = mapped_column(default=None)
    started_at: Mapped[datetime | None] = mapped_column(default=None)
    finished_at: Mapped[datetime | None] = mapped_column(default=None)
