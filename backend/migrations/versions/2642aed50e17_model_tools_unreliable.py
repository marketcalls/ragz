"""model tools unreliable

Revision ID: 2642aed50e17
Revises: bac56e452ef0
Create Date: 2026-07-20 00:13:06.903983

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2642aed50e17'
down_revision: Union[str, Sequence[str], None] = 'bac56e452ef0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "models",
        sa.Column("tools_unreliable", sa.Boolean(), server_default=sa.false(), nullable=False),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("models", "tools_unreliable")
