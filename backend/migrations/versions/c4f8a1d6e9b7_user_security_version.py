"""users.security_version

Revision ID: c4f8a1d6e9b7
Revises: 3b86726aae57
Create Date: 2026-08-15 14:05:00.000000

sec RAGZ-PUB-06: per-user security-version stamp. Bumped on password
change/reset so every access token minted BEFORE the bump stops validating
(tenancy.context.get_tenant_context compares the token's `sv` claim against
this column) even though it hasn't hit its 15-min JWT expiry yet. Refresh
tokens are already revoked on change/reset; this closes the matching gap for
still-live access tokens. server_default backfills every existing row to 0,
matching this repo's single-step ADD COLUMN NOT NULL convention (see
9ddd45cd79fb_role_template_versioning.py).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c4f8a1d6e9b7'
down_revision: Union[str, Sequence[str], None] = '3b86726aae57'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "users",
        sa.Column("security_version", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("users", "security_version")
