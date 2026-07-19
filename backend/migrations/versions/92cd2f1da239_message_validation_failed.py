"""message validation failed

Revision ID: 92cd2f1da239
Revises: 1401a520e046
Create Date: 2026-07-20 04:35:46.593058

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '92cd2f1da239'
down_revision: Union[str, Sequence[str], None] = '1401a520e046'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "messages",
        sa.Column("validation_failed", sa.Boolean(), server_default=sa.false(), nullable=False),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("messages", "validation_failed")
