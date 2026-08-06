"""bot_integrations

Revision ID: 4380e51fba67
Revises: 2ad0072c87bc
Create Date: 2026-08-07 00:19:00.393829

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '4380e51fba67'
down_revision: Union[str, Sequence[str], None] = '2ad0072c87bc'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Chat-platform bots (sub-project 2): superadmin bindings
    platform+workspace+user (bot_integrations) and their per-external-chat
    conversation mapping (bot_conversations). Credentials live encrypted in
    the secrets table under bot:{id}:token / bot:{id}:signing -- see
    modules/bots/service.py."""
    op.create_table(
        "bot_integrations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("platform", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("webhook_id", sa.Uuid(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_bot_integrations_org_id"), "bot_integrations", ["org_id"], unique=False
    )
    op.create_index(
        op.f("ix_bot_integrations_webhook_id"), "bot_integrations", ["webhook_id"], unique=True
    )
    op.create_table(
        "bot_conversations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("integration_id", sa.Uuid(), nullable=False),
        sa.Column("external_chat_id", sa.String(), nullable=False),
        sa.Column("chat_id", sa.Uuid(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["integration_id"], ["bot_integrations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["chat_id"], ["chats.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("integration_id", "external_chat_id", name="uq_bot_conversations_chat"),
    )
    op.create_index(
        op.f("ix_bot_conversations_integration_id"), "bot_conversations",
        ["integration_id"], unique=False,
    )
    op.create_index(
        op.f("ix_bot_conversations_external_chat_id"), "bot_conversations",
        ["external_chat_id"], unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_bot_conversations_external_chat_id"), table_name="bot_conversations")
    op.drop_index(op.f("ix_bot_conversations_integration_id"), table_name="bot_conversations")
    op.drop_table("bot_conversations")
    op.drop_index(op.f("ix_bot_integrations_webhook_id"), table_name="bot_integrations")
    op.drop_index(op.f("ix_bot_integrations_org_id"), table_name="bot_integrations")
    op.drop_table("bot_integrations")
