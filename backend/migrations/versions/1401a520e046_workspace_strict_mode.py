"""workspace strict mode

Revision ID: 1401a520e046
Revises: 3a82f1440dc6
Create Date: 2026-07-20 04:08:54.198884

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1401a520e046'
down_revision: Union[str, Sequence[str], None] = '3a82f1440dc6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "workspaces",
        sa.Column("strict_mode", sa.Boolean(), server_default=sa.false(), nullable=False),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("workspaces", "strict_mode")
