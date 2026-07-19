"""golden queries

Revision ID: 6ddea8ad5458
Revises: 92cd2f1da239
Create Date: 2026-07-20 05:12:56.882752

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '6ddea8ad5458'
down_revision: Union[str, Sequence[str], None] = '92cd2f1da239'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "golden_queries",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "workspace_id", sa.Uuid(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column(
            "expected_document_ids", postgresql.ARRAY(sa.Uuid()),
            nullable=False, server_default="{}",
        ),
        sa.Column("created_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_golden_queries_workspace_id"), "golden_queries", ["workspace_id"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_golden_queries_workspace_id"), table_name="golden_queries")
    op.drop_table("golden_queries")
