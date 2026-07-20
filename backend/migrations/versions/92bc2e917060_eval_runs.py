"""eval runs

Revision ID: 92bc2e917060
Revises: 6ddea8ad5458
Create Date: 2026-07-20 05:41:37.469551

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '92bc2e917060'
down_revision: Union[str, Sequence[str], None] = '6ddea8ad5458'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "eval_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "workspace_id", sa.Uuid(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("triggered_by", sa.String(), nullable=False, server_default="manual"),
        sa.Column("query_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("hit_rate", sa.Float(), nullable=True),
        sa.Column("citation_precision", sa.Float(), nullable=True),
        sa.Column("avg_faithfulness", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_eval_runs_workspace_id"), "eval_runs", ["workspace_id"], unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_eval_runs_workspace_id"), table_name="eval_runs")
    op.drop_table("eval_runs")
