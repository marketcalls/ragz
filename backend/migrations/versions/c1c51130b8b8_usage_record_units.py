"""usage record units

Revision ID: c1c51130b8b8
Revises: 7e9d7fe19f90
Create Date: 2026-08-15 23:55:16.414278

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c1c51130b8b8'
down_revision: Union[str, Sequence[str], None] = '7e9d7fe19f90'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Cost-reporting Phase 1: per-call features (rerank, web_search) record a
    # call/unit count here. Token features (chat, ingestion, embedding) leave it
    # 0 -- units are NEVER summed into any token aggregation.
    op.add_column(
        "usage_records",
        sa.Column(
            "units", sa.Integer(),
            server_default="0", nullable=False,
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("usage_records", "units")
