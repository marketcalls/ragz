"""durable resource reservations

Revision ID: a31f6d8c9e02
Revises: 6a8d2c4f1b90
Create Date: 2026-09-08

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a31f6d8c9e02"
down_revision: str | Sequence[str] | None = "6a8d2c4f1b90"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "usage_records",
        sa.Column("idempotency_key", sa.String(), nullable=True),
    )
    op.create_index(
        "uq_usage_records_idempotency_key",
        "usage_records",
        ["idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )
    op.add_column(
        "chat_attachments",
        sa.Column("size_bytes", sa.BigInteger(), nullable=False, server_default="0"),
    )
    # Historical rows predate byte accounting. Their objects can be anywhere
    # from empty to the old 50 MiB per-file ceiling; using that ceiling is a
    # conservative admission value until the read-only orphan/size inventory
    # is reviewed. Zero would silently undercount every preserved object.
    op.execute(
        sa.text(
            "UPDATE chat_attachments SET size_bytes = 52428800 "
            "WHERE size_bytes = 0"
        )
    )
    op.create_table(
        "resource_reservations",
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("size_bytes >= 0", name="ck_resource_reservations_size"),
        sa.CheckConstraint(
            "kind IN ('document', 'attachment')",
            name="ck_resource_reservations_kind",
        ),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_resource_reservations_org_kind_expiry",
        "resource_reservations",
        ["org_id", "kind", "expires_at"],
    )
    op.create_index(
        "ix_resource_reservations_user_kind_expiry",
        "resource_reservations",
        ["user_id", "kind", "expires_at"],
    )
    op.create_table(
        "attachment_cleanup_jobs",
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("chat_id", sa.Uuid(), nullable=False),
        sa.Column("attachment_id", sa.Uuid(), nullable=False),
        sa.Column("storage_key", sa.String(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(), nullable=False),
        sa.Column("last_error", sa.String(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("size_bytes >= 0", name="ck_attachment_cleanup_jobs_size"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("attachment_id", name="uq_attachment_cleanup_attachment"),
    )
    op.create_index(
        "ix_attachment_cleanup_jobs_org_id",
        "attachment_cleanup_jobs",
        ["org_id"],
    )
    op.create_index(
        "ix_attachment_cleanup_jobs_user_id",
        "attachment_cleanup_jobs",
        ["user_id"],
    )
    op.create_index(
        "ix_attachment_cleanup_jobs_next_attempt_at",
        "attachment_cleanup_jobs",
        ["next_attempt_at"],
    )
    op.create_index(
        "ix_attachment_cleanup_jobs_completed_at",
        "attachment_cleanup_jobs",
        ["completed_at"],
    )
    # Existing Qdrant points predate payload-level revision stamps. Hide every
    # indexed/vector-backed row until the existing reconciler restamps its
    # current ACL and revision; this is a bounded fail-closed availability dip.
    op.execute(
        sa.text(
            "UPDATE documents SET security_revision = security_revision + 1, "
            "index_state = 'pending' "
            "WHERE status = 'indexed' AND vectors_present = true"
        )
    )


def downgrade() -> None:
    # The upgrade's document revision bump is deliberately not reversed. A
    # downgrade may leave documents pending until the existing projection
    # reconciler runs, which is safer than pretending an older Qdrant payload
    # again matches a decremented relational revision.
    op.drop_index(
        "ix_attachment_cleanup_jobs_completed_at", table_name="attachment_cleanup_jobs"
    )
    op.drop_index(
        "ix_attachment_cleanup_jobs_next_attempt_at", table_name="attachment_cleanup_jobs"
    )
    op.drop_index(
        "ix_attachment_cleanup_jobs_org_id", table_name="attachment_cleanup_jobs"
    )
    op.drop_index(
        "ix_attachment_cleanup_jobs_user_id", table_name="attachment_cleanup_jobs"
    )
    op.drop_table("attachment_cleanup_jobs")
    op.drop_index("uq_usage_records_idempotency_key", table_name="usage_records")
    op.drop_column("usage_records", "idempotency_key")
    op.drop_index(
        "ix_resource_reservations_user_kind_expiry",
        table_name="resource_reservations",
    )
    op.drop_index(
        "ix_resource_reservations_org_kind_expiry",
        table_name="resource_reservations",
    )
    op.drop_table("resource_reservations")
    op.drop_column("chat_attachments", "size_bytes")
