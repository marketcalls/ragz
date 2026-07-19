"""workspace fallback policy

Revision ID: 38dc5fd28ce5
Revises: b3d1c7a90f42
Create Date: 2026-07-19 23:35:53.754802

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '38dc5fd28ce5'
down_revision: Union[str, Sequence[str], None] = 'b3d1c7a90f42'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "workspaces",
        sa.Column(
            "fallback_policy", sa.String(),
            server_default="general_knowledge", nullable=False,
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("workspaces", "fallback_policy")
