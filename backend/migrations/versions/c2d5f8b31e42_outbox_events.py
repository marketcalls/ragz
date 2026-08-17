"""transactional outbox

Architecture review P1: domain services committed state and THEN called
.apply_async(). A broker outage or crash between those points left durable
database state with no durable work behind it -- an upload stuck at "queued"
forever with nothing to retry from. An outbox row is written inside the caller's
transaction, so the domain change and the intent to act on it commit together.

Revision ID: c2d5f8b31e42
Revises: b1c4e7a20d31
Create Date: 2026-08-17

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'c2d5f8b31e42'
down_revision: Union[str, Sequence[str], None] = 'b1c4e7a20d31'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "outbox_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("topic", sa.String(), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("queue", sa.String(), nullable=False, server_default="default"),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.String(), nullable=True),
        sa.Column("available_at", sa.DateTime(), nullable=False),
        sa.Column("dispatched_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "status IN ('pending', 'dispatched', 'failed')",
            name="ck_outbox_events_status",
        ),
    )
    # The dispatcher's only query: due pending work, oldest first. Partial,
    # because dispatched rows accumulate and it never reads them.
    op.create_index(
        "ix_outbox_events_due",
        "outbox_events",
        ["available_at"],
        unique=False,
        postgresql_where=sa.text("status = 'pending'"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_outbox_events_due", table_name="outbox_events")
    op.drop_table("outbox_events")
