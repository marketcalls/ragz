"""message auditor scores

Revision ID: 3a82f1440dc6
Revises: 4a2c876f0a13
Create Date: 2026-07-20 03:43:44.683772

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3a82f1440dc6'
down_revision: Union[str, Sequence[str], None] = '4a2c876f0a13'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("messages", sa.Column("grounding_score", sa.Float(), nullable=True))
    op.add_column("messages", sa.Column("completeness_score", sa.Float(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("messages", "completeness_score")
    op.drop_column("messages", "grounding_score")
