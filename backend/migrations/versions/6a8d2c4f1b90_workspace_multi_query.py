"""workspace multi-query retrieval toggle

Revision ID: 6a8d2c4f1b90
Revises: 9cc2f9645fc0
Create Date: 2026-08-21

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "6a8d2c4f1b90"
down_revision: str | Sequence[str] | None = "9cc2f9645fc0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add a default-off, workspace-scoped retrieval behavior flag."""
    op.add_column(
        "workspaces",
        sa.Column(
            "multi_query_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    """Remove the multi-query retrieval flag."""
    op.drop_column("workspaces", "multi_query_enabled")
