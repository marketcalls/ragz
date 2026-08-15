"""generative UI blocks

Revision ID: 67ff1fdb0389
Revises: c4f8a1d6e9b7
Create Date: 2026-08-15 15:20:00.000000

Phase 2 in-chat generative UI (design doc
docs/superpowers/specs/2026-08-15-in-chat-generative-ui-design.md, §2/§4):
- workspaces.generative_ui_enabled: per-workspace opt-in (default OFF,
  mirrors web_search_enabled's ADD COLUMN NOT NULL + server_default pattern)
  gating the extra "visualize" model call in chat/service.py::stream_reply.
- messages.blocks_json: nullable JSONB, the assistant message's validated
  (blocks.py::validate_blocks) block array, or null when the visualize step
  never ran / emitted nothing. Existing rows backfill to null.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '67ff1fdb0389'
down_revision: Union[str, Sequence[str], None] = 'c4f8a1d6e9b7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "workspaces",
        sa.Column(
            "generative_ui_enabled", sa.Boolean(), server_default=sa.false(), nullable=False
        ),
    )
    op.add_column(
        "messages",
        sa.Column("blocks_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("messages", "blocks_json")
    op.drop_column("workspaces", "generative_ui_enabled")
