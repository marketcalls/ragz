"""document enriched flag

Revision ID: ee1fcf572880
Revises: 2217d3bed0e2
Create Date: 2026-07-20 08:13:18.758480

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'ee1fcf572880'
down_revision: Union[str, Sequence[str], None] = '2217d3bed0e2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "documents",
        sa.Column(
            "enriched", sa.Boolean(),
            server_default=sa.false(), nullable=False,
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("documents", "enriched")
