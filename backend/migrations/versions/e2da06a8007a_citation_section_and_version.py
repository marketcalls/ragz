"""citation section and version

Revision ID: e2da06a8007a
Revises: 8512e85dcab5
Create Date: 2026-07-19 12:15:27.749870

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e2da06a8007a'
down_revision: Union[str, Sequence[str], None] = '8512e85dcab5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema.

    Plan H (CHAT-4): citations gain the chunk's section path and the
    document's version at citation time. `version` needs a server_default so
    existing rows backfill to 1 (pre-H citations predate version lineage,
    same rationale as document.version in 8512e85dcab5).
    """
    op.add_column('citations', sa.Column('section', sa.Text(), nullable=True))
    op.add_column(
        'citations', sa.Column('version', sa.Integer(), server_default='1', nullable=False)
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('citations', 'version')
    op.drop_column('citations', 'section')
