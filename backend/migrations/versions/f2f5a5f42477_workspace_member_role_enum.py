"""workspace_member_role_enum

Revision ID: f2f5a5f42477
Revises: f0a1b2c3d4e5
Create Date: 2026-08-11 05:42:47.439674

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f2f5a5f42477'
down_revision: Union[str, Sequence[str], None] = 'f0a1b2c3d4e5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """RBAC-08: role was an unvalidated free string, never read by any
    authorization decision. Backfill every non-conforming value to
    'contributor' (the safe, capability-preserving floor -- role has never
    gated anything until this program, so no existing behavior changes), then
    ensure every workspace with at least one member has exactly one 'owner'
    (promoting the lowest user_id deterministically where none exists -- there
    is no created-by/timestamp column on this table to pick a "first" member
    from), then add the CHECK constraint."""
    conn = op.get_bind()
    conn.execute(
        sa.text(
            "UPDATE workspace_members SET role = 'contributor' "
            "WHERE role NOT IN ('owner', 'manager', 'contributor', 'viewer')"
        )
    )
    conn.execute(
        sa.text(
            """
            UPDATE workspace_members wm SET role = 'owner'
            WHERE wm.user_id = (
                -- Postgres has no MIN()/MAX() aggregate registered for uuid,
                -- so pick the lowest user_id via ORDER BY + LIMIT 1 instead.
                SELECT wm2.user_id FROM workspace_members wm2
                WHERE wm2.workspace_id = wm.workspace_id
                ORDER BY wm2.user_id ASC
                LIMIT 1
            )
            AND NOT EXISTS (
                SELECT 1 FROM workspace_members wm3
                WHERE wm3.workspace_id = wm.workspace_id AND wm3.role = 'owner'
            )
            """
        )
    )
    op.create_check_constraint(
        "ck_workspace_members_role",
        "workspace_members",
        "role IN ('owner', 'manager', 'contributor', 'viewer')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_workspace_members_role", "workspace_members", type_="check")
