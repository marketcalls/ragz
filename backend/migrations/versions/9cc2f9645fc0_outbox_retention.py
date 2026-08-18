"""outbox retention

Every successful dispatch left its row in outbox_events forever, with no
retention or archival path. It is the busiest insert path in the system -- one
row per upload, delete, reindex and eval -- so the table grew without bound in
storage, backups and vacuum work (Cubic P2).

modules/outbox/service.py::purge_dispatched deletes dispatched rows past a
7-day window, driven daily by the outbox.purge_dispatched beat task. This index
is its only query: partial on status='dispatched', the mirror of
ix_outbox_events_due, which is partial on status='pending'. Without it the daily
sweep would sequentially scan the largest table in the database.

Revision ID: 9cc2f9645fc0
Revises: 0bf293309b11
Create Date: 2026-08-18

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '9cc2f9645fc0'
down_revision: Union[str, Sequence[str], None] = '0bf293309b11'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_index(
        "ix_outbox_events_dispatched_at",
        "outbox_events",
        ["dispatched_at"],
        postgresql_where=sa.text("status = 'dispatched'"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_outbox_events_dispatched_at", table_name="outbox_events")
