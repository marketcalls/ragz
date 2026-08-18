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

import sqlalchemy as sa
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


#: Preflight (Cubic P1). Postgres VALIDATES every existing row when a foreign
#: key is added, so a database that already contains a cross-tenant row -- the
#: exact bug these constraints exist to prevent, which nothing stopped until now
#: -- aborts the whole upgrade with a bare constraint-violation message and no
#: indication of WHICH rows are at fault. Report them first, actionably.
def _preflight(table: str, column: str, ref: str) -> None:
    conn = op.get_bind()
    bad = conn.execute(
        sa.text(
            f"SELECT t.id FROM {table} t "  # noqa: S608 - identifiers are module constants
            f"LEFT JOIN {ref} r ON r.id = t.{column} AND r.org_id = t.org_id "
            f"WHERE t.{column} IS NOT NULL AND r.id IS NULL LIMIT 20"
        )
    ).scalars().all()
    if bad:
        raise RuntimeError(
            f"{table}.{column} has rows referencing a DIFFERENT org's {ref}: "
            f"{[str(b) for b in bad]}"
            f"{' (first 20 shown)' if len(bad) == 20 else ''}. "
            f"These are cross-tenant rows -- a data-integrity incident, not a "
            f"migration problem. Investigate and repair or quarantine them, then "
            f"re-run this migration."
        )


def upgrade() -> None:
    """Upgrade schema."""
    for table, column in _WORKSPACE_FKS:
        _preflight(table, column, "workspaces")
    for table, column in _USER_FKS:
        _preflight(table, column, "users")
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
