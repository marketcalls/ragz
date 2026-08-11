"""role status enums

RBAC-11: CHECK constraints on `users.role`, `invitations.role`, and
`documents.status`. Value lists confirmed exhaustive against every literal
assignment/comparison in the codebase (modules/documents, worker/tasks.py,
modules/auth/service.py + schemas.py's Literal types, bootstrap.py's
superadmin seed) as of this migration:
  - users.role: superadmin (bootstrap.py), admin (UserPatch/InvitationCreate
    schemas), user (default + JIT provisioning).
  - invitations.role: admin, user (InvitationCreate schema Literal).
  - documents.status: queued (model default), processing, indexed, failed
    (ingest.py), deleting (folders.py/api/routes/documents.py) -- closes a
    real gap since those write paths write the literal string with no
    schema-level guarantee against a typo silently creating an unfilterable
    status.
Note: workspace_members.role already has its own CHECK
(ck_workspace_members_role, owner|manager|contributor|viewer) added
separately in modules/tenancy/models.py -- out of scope here.

Revision ID: 0521b696bbe9
Revises: fe047b5cdf08
Create Date: 2026-08-11 06:15:57.715140

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '0521b696bbe9'
down_revision: Union[str, Sequence[str], None] = 'fe047b5cdf08'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_check_constraint(
        "ck_users_role", "users", "role IN ('superadmin', 'admin', 'user')",
    )
    op.create_check_constraint(
        "ck_invitations_role", "invitations", "role IN ('admin', 'user')",
    )
    op.create_check_constraint(
        "ck_documents_status", "documents",
        "status IN ('queued', 'processing', 'indexed', 'failed', 'deleting')",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint("ck_documents_status", "documents", type_="check")
    op.drop_constraint("ck_invitations_role", "invitations", type_="check")
    op.drop_constraint("ck_users_role", "users", type_="check")
