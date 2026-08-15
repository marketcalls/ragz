"""usage record workspace_id

Revision ID: d7f2a9c4e1b8
Revises: c1c51130b8b8
Create Date: 2026-08-16 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd7f2a9c4e1b8'
down_revision: Union[str, Sequence[str], None] = 'c1c51130b8b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Cost-reporting Phase 2a: tag every usage-ledger row with the workspace it
    # belongs to (department = workspace) so department-scoped reports can
    # aggregate by workspace. Pure reporting dimension -- NEVER summed into any
    # token/units aggregation. Nullable: platform-level ops have no workspace.
    op.add_column(
        "usage_records",
        sa.Column("workspace_id", sa.Uuid(), nullable=True),
    )
    op.create_index(
        "ix_usage_workspace_created",
        "usage_records",
        ["workspace_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_usage_workspace_created", table_name="usage_records")
    op.drop_column("usage_records", "workspace_id")
