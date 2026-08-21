"""outbox traceparent

Revision ID: 6bc57af3b7b3
Revises: 9cc2f9645fc0
Create Date: 2026-08-19 07:41:15.115675

Hand-written, NOT the autogenerate output. Running --autogenerate here also
emitted drops for eval_runs, bot_integrations, bot_conversations,
golden_queries and three api_keys foreign keys -- it compared against metadata
that did not have those models loaded, so it read them as removed. Applying
that would have destroyed four tables to add one nullable column. Only the
column belongs in this revision.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '6bc57af3b7b3'
down_revision: str | Sequence[str] | None = '9cc2f9645fc0'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # Nullable, no backfill: in-flight events published before this deploy
    # simply have no trace context, and their consumers start their own trace.
    # A NOT NULL default would be a lie -- there is no correct traceparent to
    # invent for an event whose originating request is already over.
    op.add_column('outbox_events', sa.Column('traceparent', sa.String(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('outbox_events', 'traceparent')
