"""workspace embedding_model_id FK (replaces the inert embedding_model string)

Revision ID: e2f9a5b3c7d4
Revises: d1e8f4a2b6c3
Create Date: 2026-07-24 09:30:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'e2f9a5b3c7d4'
down_revision: Union[str, Sequence[str], None] = 'd1e8f4a2b6c3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

LOCAL_EMBEDDING_MODEL_ID = "00000000-0000-4000-8000-000000000001"


def upgrade() -> None:
    """Upgrade schema."""
    # Every existing workspace's inert embedding_model column has always been
    # "bge-m3" (the only value ever written) -- backfill every row to the
    # seeded local model in the SAME statement that adds the column, via
    # server_default, matching this repo's existing single-step ADD COLUMN
    # NOT NULL convention (see fcf2710fd015_model_supports_vision.py).
    op.add_column(
        "workspaces",
        sa.Column(
            "embedding_model_id", sa.Uuid(),
            server_default=sa.text(f"'{LOCAL_EMBEDDING_MODEL_ID}'"), nullable=False,
        ),
    )
    op.create_foreign_key(
        "fk_workspaces_embedding_model_id", "workspaces", "models",
        ["embedding_model_id"], ["id"],
    )
    op.drop_column("workspaces", "embedding_model")


def downgrade() -> None:
    """Downgrade schema."""
    op.add_column(
        "workspaces", sa.Column("embedding_model", sa.String(), server_default="bge-m3", nullable=False)
    )
    op.drop_constraint("fk_workspaces_embedding_model_id", "workspaces", type_="foreignkey")
    op.drop_column("workspaces", "embedding_model_id")
