"""content manager admin grant

Revision ID: f0a1b2c3d4e5
Revises: d4a26ef9adfe
Create Date: 2026-08-11 06:00:00.000000

"""
import uuid
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'f0a1b2c3d4e5'
down_revision: Union[str, Sequence[str], None] = 'd4a26ef9adfe'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_CONTENT_MANAGER_ID = uuid.UUID("00000000-0000-0000-0000-000000000c03")


def upgrade() -> None:
    """RBAC-05: removing admin's automatic content-ACL bypass must be paired
    with this migration -- every EXISTING admin/superadmin account keeps
    today's unrestricted content access via an explicit Content Manager grant
    (seeded at d4a26ef9adfe), so nobody legitimately relying on it loses
    access at deploy time. Only accounts with no custom_role_id are touched --
    an admin who already has some other explicit grant keeps it unchanged."""
    op.execute(
        sa.text(
            "UPDATE users SET custom_role_id = :id "
            "WHERE role IN ('admin', 'superadmin') AND custom_role_id IS NULL"
        ).bindparams(sa.bindparam("id", value=_CONTENT_MANAGER_ID, type_=sa.Uuid()))
    )


def downgrade() -> None:
    """Reverse the grant: null out custom_role_id only where it points at the
    Content Manager template this migration set (leaves other grants intact)."""
    op.execute(
        sa.text(
            "UPDATE users SET custom_role_id = NULL "
            "WHERE role IN ('admin', 'superadmin') AND custom_role_id = :id"
        ).bindparams(sa.bindparam("id", value=_CONTENT_MANAGER_ID, type_=sa.Uuid()))
    )
