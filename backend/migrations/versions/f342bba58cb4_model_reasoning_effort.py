"""model reasoning effort

Revision ID: f342bba58cb4
Revises: e668194c6750
Create Date: 2026-07-20 13:53:08.635321

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f342bba58cb4'
down_revision: Union[str, Sequence[str], None] = 'e668194c6750'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "models",
        sa.Column("supports_reasoning", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.add_column(
        "models",
        sa.Column(
            "default_reasoning_effort", sa.String(), server_default="off", nullable=False
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("models", "default_reasoning_effort")
    op.drop_column("models", "supports_reasoning")
