"""tenant fk constraints

Revision ID: fe047b5cdf08
Revises: f2f5a5f42477
Create Date: 2026-08-11 06:06:27.279370

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'fe047b5cdf08'
down_revision: Union[str, Sequence[str], None] = 'f2f5a5f42477'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """RBAC-11: api_keys/bot_integrations had NO foreign keys on org_id/
    user_id/workspace_id/created_by. Adds a UNIQUE(id, org_id) on the
    referenced tables (users, workspaces) so a composite FK can prove BOTH
    that the referenced row exists AND that it belongs to the SAME org as the
    dependent row -- a plain single-column FK on workspace_id alone cannot
    express "and it's the same org." All four columns involved (org_id,
    user_id, workspace_id, created_by) are NOT NULL on both tables, so this
    composite FK is fully enforced (Postgres MATCH SIMPLE only skips
    enforcement when one of the paired columns is NULL, which can't happen
    here)."""
    op.create_unique_constraint("uq_users_id_org_id", "users", ["id", "org_id"])
    op.create_unique_constraint("uq_workspaces_id_org_id", "workspaces", ["id", "org_id"])

    for table in ("api_keys", "bot_integrations"):
        op.create_foreign_key(
            f"fk_{table}_user_org", table, "users",
            ["user_id", "org_id"], ["id", "org_id"],
        )
        op.create_foreign_key(
            f"fk_{table}_workspace_org", table, "workspaces",
            ["workspace_id", "org_id"], ["id", "org_id"],
        )
        op.create_foreign_key(
            f"fk_{table}_created_by", table, "users", ["created_by"], ["id"],
        )


def downgrade() -> None:
    """Downgrade schema."""
    for table in ("api_keys", "bot_integrations"):
        op.drop_constraint(f"fk_{table}_created_by", table, type_="foreignkey")
        op.drop_constraint(f"fk_{table}_workspace_org", table, type_="foreignkey")
        op.drop_constraint(f"fk_{table}_user_org", table, type_="foreignkey")
    op.drop_constraint("uq_workspaces_id_org_id", "workspaces", type_="unique")
    op.drop_constraint("uq_users_id_org_id", "users", type_="unique")
