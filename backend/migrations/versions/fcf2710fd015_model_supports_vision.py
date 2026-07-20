"""model supports vision

Revision ID: fcf2710fd015
Revises: 36b6ae8fcc01
Create Date: 2026-07-20 21:15:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'fcf2710fd015'
down_revision: Union[str, Sequence[str], None] = '36b6ae8fcc01'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "models",
        sa.Column("supports_vision", sa.Boolean(), server_default=sa.false(), nullable=False),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("models", "supports_vision")
