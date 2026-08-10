"""seed contributor role

Revision ID: c78eddf6863e
Revises: 4380e51fba67
Create Date: 2026-08-11 00:00:00.000000

"""
import uuid
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c78eddf6863e'
down_revision: Union[str, Sequence[str], None] = '4380e51fba67'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_CONTRIBUTOR_ID = uuid.UUID("00000000-0000-0000-0000-000000000c01")
_CONTRIBUTOR_PERMISSIONS = [
    "workspace.read",
    "documents.list", "documents.content.read", "documents.upload", "documents.delete",
    "documents.move", "documents.pin", "search.execute", "chat.read", "chat.generate",
    "chat.attachments.create", "chat.delete",
    # Legacy flag still enforced at runtime by the chat routes'
    # require_action("chat.use") gate (retired only in a later task). MUST be
    # present so an existing user reassigned to this role at migration time
    # does not lose chat access -- Contributor is a strict superset of the
    # current DEFAULT_USER_PERMISSIONS (which still carries chat.use).
    "chat.use",
]


def upgrade() -> None:
    """RBAC-04: the deny-by-default baseline shrinks DEFAULT_USER_PERMISSIONS
    to read-only. Every account that was relying on the old broad default
    (role='user', no custom_role_id) gets an EXPLICIT 'Contributor' role
    template carrying exactly that old capability, so nobody's access
    regresses at deploy time -- see design doc decision 1."""
    op.execute(
        sa.text(
            "INSERT INTO role_templates (id, created_at, name, description, permissions) "
            "VALUES (:id, now(), :name, :description, :permissions) "
            "ON CONFLICT (name) DO NOTHING"
        ).bindparams(
            sa.bindparam("id", value=_CONTRIBUTOR_ID, type_=sa.Uuid()),
            sa.bindparam("name", value="Contributor", type_=sa.String()),
            sa.bindparam(
                "description",
                value=(
                    "Migrated baseline (RBAC-04): upload, delete, and chat -- the "
                    "capability every role='user' account already had before the "
                    "deny-by-default change."
                ),
                type_=sa.String(),
            ),
            sa.bindparam(
                "permissions", value=_CONTRIBUTOR_PERMISSIONS, type_=sa.ARRAY(sa.String())
            ),
        )
    )
    op.execute(
        sa.text(
            "UPDATE users SET custom_role_id = :id "
            "WHERE role = 'user' AND custom_role_id IS NULL"
        ).bindparams(sa.bindparam("id", value=_CONTRIBUTOR_ID, type_=sa.Uuid()))
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute(
        sa.text("UPDATE users SET custom_role_id = NULL WHERE custom_role_id = :id").bindparams(
            sa.bindparam("id", value=_CONTRIBUTOR_ID, type_=sa.Uuid())
        )
    )
    op.execute(
        sa.text("DELETE FROM role_templates WHERE id = :id").bindparams(
            sa.bindparam("id", value=_CONTRIBUTOR_ID, type_=sa.Uuid())
        )
    )
