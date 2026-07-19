"""message grounding

Revision ID: bac56e452ef0
Revises: 38dc5fd28ce5
Create Date: 2026-07-19 23:40:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'bac56e452ef0'
down_revision: Union[str, Sequence[str], None] = '38dc5fd28ce5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "messages",
        sa.Column("grounding", sa.String(), server_default="documents", nullable=False),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("messages", "grounding")
