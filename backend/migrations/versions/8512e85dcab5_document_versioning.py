"""document versioning

Revision ID: 8512e85dcab5
Revises: aa6a0eb97c23
Create Date: 2026-07-19 10:09:14.359605

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8512e85dcab5'
down_revision: Union[str, Sequence[str], None] = 'aa6a0eb97c23'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema.

    Autogenerate can't backfill: columns land nullable/with server defaults,
    existing rows are backfilled, then constraints are tightened.

    Backfill semantics: every pre-H document is live, so `is_current` backfills
    TRUE for existing rows. The ORM default for NEW rows is False — promotion
    (Task 6) owns the flag from here on. `lineage_id` backfills to each row's
    own id (pre-H documents predate lineage tracking, so each is its own
    lineage root). `vectors_present` backfills TRUE only for already-indexed
    docs, mirroring their real Qdrant state.
    """
    op.add_column("documents", sa.Column("version", sa.Integer(), server_default="1", nullable=False))
    op.add_column("documents", sa.Column("lineage_id", sa.Uuid(), nullable=True))
    op.add_column("documents", sa.Column("supersedes_document_id", sa.Uuid(), nullable=True))
    op.add_column("documents", sa.Column("is_current", sa.Boolean(), server_default=sa.true(), nullable=False))
    op.add_column("documents", sa.Column("approved", sa.Boolean(), server_default=sa.false(), nullable=False))
    op.add_column("documents", sa.Column("vectors_present", sa.Boolean(), server_default=sa.false(), nullable=False))
    op.execute("UPDATE documents SET lineage_id = id")
    op.execute("UPDATE documents SET vectors_present = TRUE WHERE status = 'indexed'")
    op.alter_column("documents", "lineage_id", nullable=False)
    op.create_index(op.f("ix_documents_lineage_id"), "documents", ["lineage_id"])
    op.create_index(op.f("ix_documents_is_current"), "documents", ["is_current"])
    op.create_foreign_key(
        "fk_documents_supersedes", "documents", "documents",
        ["supersedes_document_id"], ["id"], ondelete="SET NULL",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint("fk_documents_supersedes", "documents", type_="foreignkey")
    op.drop_index(op.f("ix_documents_is_current"), table_name="documents")
    op.drop_index(op.f("ix_documents_lineage_id"), table_name="documents")
    op.drop_column("documents", "vectors_present")
    op.drop_column("documents", "approved")
    op.drop_column("documents", "is_current")
    op.drop_column("documents", "supersedes_document_id")
    op.drop_column("documents", "lineage_id")
    op.drop_column("documents", "version")
