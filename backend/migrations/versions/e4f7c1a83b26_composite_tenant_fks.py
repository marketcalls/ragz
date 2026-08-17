"""composite same-tenant foreign keys

Architecture review P1 "Postgres tenant isolation is application-convention
based". documents/chats/folders/usage_records carried only SINGLE-column FKs on
workspace_id and user_id, which prove the referenced row EXISTS but say nothing
about whose it is. Nothing in the database stopped a row in org A pointing at a
workspace in org B; the only thing preventing it was every query path
remembering to go through TenantContext.

fe047b5cdf08 established the pattern for api_keys/bot_integrations and left the
UNIQUE(id, org_id) targets behind. This applies it to the tables that actually
hold tenant data. All the paired columns are NOT NULL (except
usage_records.workspace_id, noted below), so Postgres enforces these fully --
MATCH SIMPLE only skips enforcement when one of the paired columns is NULL.

This is defence in depth, not a replacement for TenantContext: it makes a whole
class of bug impossible to persist rather than merely unlikely to be written.

Revision ID: e4f7c1a83b26
Revises: d3e6a9c42f15
Create Date: 2026-08-17

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'e4f7c1a83b26'
down_revision: Union[str, Sequence[str], None] = 'd3e6a9c42f15'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: (table, local column, referenced table, its id column). The org_id half is
#: appended to both sides -- that pairing IS the same-tenant proof.
_WORKSPACE_FKS = [
    ("documents", "workspace_id"),
    ("chats", "workspace_id"),
    ("folders", "workspace_id"),
    # usage_records is deliberately EXCLUDED: it has no foreign keys at all
    # today, so adding a composite one would introduce referential integrity
    # that never existed rather than just the same-tenant pairing. That is a
    # separate, riskier change (it can fail on historical rows) and belongs in
    # its own migration.
]
_USER_FKS = [
    ("documents", "created_by"),
    ("chats", "user_id"),
    ("folders", "created_by"),
]


def upgrade() -> None:
    """Upgrade schema."""
    for table, column in _WORKSPACE_FKS:
        op.create_foreign_key(
            f"fk_{table}_{column}_org", table, "workspaces",
            [column, "org_id"], ["id", "org_id"],
        )
    for table, column in _USER_FKS:
        op.create_foreign_key(
            f"fk_{table}_{column}_org", table, "users",
            [column, "org_id"], ["id", "org_id"],
        )


def downgrade() -> None:
    """Downgrade schema."""
    for table, column in _WORKSPACE_FKS + _USER_FKS:
        op.drop_constraint(f"fk_{table}_{column}_org", table, type_="foreignkey")
