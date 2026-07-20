"""message feedback

Revision ID: 6d96113321ac
Revises: f342bba58cb4
Create Date: 2026-07-20 17:15:49.384054

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '6d96113321ac'
down_revision: Union[str, Sequence[str], None] = 'f342bba58cb4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "message_feedback",
        sa.Column("message_id", sa.Uuid(), nullable=False),
        sa.Column("rating", sa.String(), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["message_id"], ["messages.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("message_id"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("message_feedback")
