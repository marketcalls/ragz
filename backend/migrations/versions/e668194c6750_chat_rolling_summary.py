"""chat rolling summary

Revision ID: e668194c6750
Revises: ee1fcf572880
Create Date: 2026-07-20 09:02:45.082947

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e668194c6750'
down_revision: Union[str, Sequence[str], None] = 'ee1fcf572880'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("chats", sa.Column("summary", sa.Text(), nullable=True))
    op.add_column("chats", sa.Column("summary_upto_message_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_chats_summary_upto_message", "chats", "messages",
        ["summary_upto_message_id"], ["id"], ondelete="SET NULL",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint("fk_chats_summary_upto_message", "chats", type_="foreignkey")
    op.drop_column("chats", "summary_upto_message_id")
    op.drop_column("chats", "summary")
