"""org profile fields

Revision ID: a8b9d8757e3c
Revises: e3b9f7a1c6d2
Create Date: 2026-08-16 13:21:59.442600

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a8b9d8757e3c'
down_revision: Union[str, Sequence[str], None] = 'e3b9f7a1c6d2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("organizations", sa.Column("contact_email", sa.String(), nullable=True))
    op.add_column("organizations", sa.Column("industry", sa.String(), nullable=True))
    op.add_column("organizations", sa.Column("company_size", sa.String(), nullable=True))
    op.add_column("organizations", sa.Column("country", sa.String(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("organizations", "country")
    op.drop_column("organizations", "company_size")
    op.drop_column("organizations", "industry")
    op.drop_column("organizations", "contact_email")
