"""ingest job heartbeat

reconcile_stuck_documents decided a document was abandoned from
Document.updated_at, but nothing in the pipeline touches that column while a
stage runs: run_embed_upsert commits IngestJob.progress after every batch and
leaves the document row alone. A genuinely long parse+embed therefore looked
identical to a dead worker, and the sweep re-published work that was actively
running (Cubic P1).

ingest_jobs.updated_at carries onupdate=naive_utc in the ORM, so the per-batch
progress commit that already happens becomes a liveness signal at no extra
write cost. The reconciler now keys off the newest job heartbeat and only falls
back to documents.updated_at for rows that have no job at all ("queued" before
the first stage starts, and "deleting").

Backfilled from started_at, then created_at, so pre-existing rows get a
plausible heartbeat instead of NULL or "now" -- stamping "now" would hide
genuinely stuck rows from the very first sweep after deploy.

Revision ID: d21fa2c66844
Revises: f5a8d2e91c47
Create Date: 2026-08-18

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'd21fa2c66844'
down_revision: Union[str, Sequence[str], None] = 'f5a8d2e91c47'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "ingest_jobs",
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.execute(
        "UPDATE ingest_jobs SET updated_at = COALESCE(started_at, created_at)"
        " WHERE updated_at IS NULL"
    )
    op.alter_column("ingest_jobs", "updated_at", nullable=False)
    # The reconciler's hot path is "newest job per stuck document".
    op.create_index(
        "ix_ingest_jobs_document_id_updated_at",
        "ingest_jobs",
        ["document_id", "updated_at"],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_ingest_jobs_document_id_updated_at", table_name="ingest_jobs")
    op.drop_column("ingest_jobs", "updated_at")
