"""model is utility

Revision ID: 4a2c876f0a13
Revises: 8d9f2fa4333b
Create Date: 2026-07-20 03:13:34.629697

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '4a2c876f0a13'
down_revision: Union[str, Sequence[str], None] = '8d9f2fa4333b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "models",
        sa.Column("is_utility", sa.Boolean(), server_default=sa.false(), nullable=False),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("models", "is_utility")
