"""document pinned

Revision ID: 0eed41c280a6
Revises: 472f2dd1cfd0
Create Date: 2026-07-19 00:17:13.191067

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0eed41c280a6'
down_revision: Union[str, Sequence[str], None] = '472f2dd1cfd0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("documents", sa.Column("pinned", sa.Boolean(), server_default=sa.false(), nullable=False))
    op.create_index(op.f("ix_documents_pinned"), "documents", ["pinned"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_documents_pinned"), table_name="documents")
    op.drop_column("documents", "pinned")
