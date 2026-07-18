"""workspace retrieval settings

Revision ID: 472f2dd1cfd0
Revises: 30fd53053c48
Create Date: 2026-07-18 23:56:24.016960

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '472f2dd1cfd0'
down_revision: Union[str, Sequence[str], None] = '30fd53053c48'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("workspaces", sa.Column("top_k", sa.Integer(), server_default="8", nullable=False))
    op.add_column(
        "workspaces", sa.Column("rerank_enabled", sa.Boolean(), server_default=sa.false(), nullable=False)
    )
    op.add_column("workspaces", sa.Column("system_prompt_override", sa.Text(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("workspaces", "system_prompt_override")
    op.drop_column("workspaces", "rerank_enabled")
    op.drop_column("workspaces", "top_k")
