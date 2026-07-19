"""model catalog position

Revision ID: b3d1c7a90f42
Revises: 61aa0eede468
Create Date: 2026-07-19 16:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b3d1c7a90f42'
down_revision: Union[str, Sequence[str], None] = '61aa0eede468'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'model_catalog',
        sa.Column('position', sa.Integer(), nullable=False, server_default='0'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('model_catalog', 'position')
