"""web search support

Revision ID: 8d9f2fa4333b
Revises: 2642aed50e17
Create Date: 2026-07-20 01:49:21.789129

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8d9f2fa4333b'
down_revision: Union[str, Sequence[str], None] = '2642aed50e17'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "workspaces",
        sa.Column("web_search_enabled", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.add_column("citations", sa.Column("url", sa.Text(), nullable=True))
    op.alter_column("citations", "document_id", existing_type=sa.Uuid(), nullable=True)


def downgrade() -> None:
    """Downgrade schema."""
    op.alter_column("citations", "document_id", existing_type=sa.Uuid(), nullable=False)
    op.drop_column("citations", "url")
    op.drop_column("workspaces", "web_search_enabled")
