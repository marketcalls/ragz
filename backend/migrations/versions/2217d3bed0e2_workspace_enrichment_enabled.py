"""workspace enrichment enabled

Revision ID: 2217d3bed0e2
Revises: 92bc2e917060
Create Date: 2026-07-20 07:52:07.697324

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2217d3bed0e2'
down_revision: Union[str, Sequence[str], None] = '92bc2e917060'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "workspaces",
        sa.Column(
            "enrichment_enabled", sa.Boolean(),
            server_default=sa.false(), nullable=False,
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("workspaces", "enrichment_enabled")
