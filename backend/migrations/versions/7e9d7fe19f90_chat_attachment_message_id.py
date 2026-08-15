"""chat attachment message_id (transcript rendering)

Revision ID: 7e9d7fe19f90
Revises: 67ff1fdb0389
Create Date: 2026-08-15 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7e9d7fe19f90'
down_revision: Union[str, Sequence[str], None] = '67ff1fdb0389'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "chat_attachments", sa.Column("message_id", sa.Uuid(), nullable=True)
    )
    op.create_index(
        op.f("ix_chat_attachments_message_id"), "chat_attachments", ["message_id"],
        unique=False,
    )
    op.create_foreign_key(
        "fk_chat_attachments_message_id", "chat_attachments", "messages",
        ["message_id"], ["id"], ondelete="SET NULL",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(
        "fk_chat_attachments_message_id", "chat_attachments", type_="foreignkey"
    )
    op.drop_index(op.f("ix_chat_attachments_message_id"), table_name="chat_attachments")
    op.drop_column("chat_attachments", "message_id")
