"""reembed_jobs table (DOC-10)

Revision ID: f3a0b6c4d8e5
Revises: e2f9a5b3c7d4
Create Date: 2026-07-24 10:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'f3a0b6c4d8e5'
down_revision: Union[str, Sequence[str], None] = 'e2f9a5b3c7d4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "reembed_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("old_embedding_model_id", sa.Uuid(), nullable=False),
        sa.Column("new_embedding_model_id", sa.Uuid(), nullable=False),
        sa.Column("documents_total", sa.Integer(), nullable=False),
        sa.Column("documents_done", sa.Integer(), nullable=False),
        sa.Column("error", sa.String(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["old_embedding_model_id"], ["models.id"]),
        sa.ForeignKeyConstraint(["new_embedding_model_id"], ["models.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_reembed_jobs_workspace_id"), "reembed_jobs", ["workspace_id"], unique=False
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_reembed_jobs_workspace_id"), table_name="reembed_jobs")
    op.drop_table("reembed_jobs")
