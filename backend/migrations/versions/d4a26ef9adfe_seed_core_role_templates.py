"""seed core role templates

Revision ID: d4a26ef9adfe
Revises: c78eddf6863e
Create Date: 2026-08-11 04:59:40.948945

"""
import uuid
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'd4a26ef9adfe'
down_revision: Union[str, Sequence[str], None] = 'c78eddf6863e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TEMPLATES = [
    (uuid.UUID("00000000-0000-0000-0000-000000000c02"), "Viewer",
     "Read-only: browse and search the corpus, chat -- no mutation.",
     ["workspace.read", "documents.list", "documents.content.read", "search.execute",
      "chat.read", "chat.generate"]),
    (uuid.UUID("00000000-0000-0000-0000-000000000c03"), "Content Manager",
     "Library management: upload/delete/move/pin, ACL and approval authority, "
     "including the explicit content-ACL-bypass grant (RBAC-05).",
     ["documents.list", "documents.content.read", "documents.upload", "documents.delete",
      "documents.move", "documents.pin", "documents.acl.manage", "documents.approve",
      "documents.acl.bypass", "folders.read", "folders.create", "folders.update",
      "folders.delete"]),
    (uuid.UUID("00000000-0000-0000-0000-000000000c04"), "Workspace Owner",
     "Full workspace governance: settings, re-embed, and membership.",
     ["workspace.read", "workspace.configure", "workspace.reembed",
      "workspace.members.read", "workspace.members.manage", "documents.list",
      "documents.content.read"]),
    (uuid.UUID("00000000-0000-0000-0000-000000000c05"), "Workspace Manager",
     "Workspace settings and membership, without re-embed authority.",
     ["workspace.read", "workspace.configure", "workspace.members.read",
      "workspace.members.manage", "documents.list", "documents.content.read"]),
    (uuid.UUID("00000000-0000-0000-0000-000000000c06"), "IAM Admin",
     "User/group administration -- no automatic content-ACL bypass, no audit access.",
     ["users.read", "users.invite", "users.activate", "users.role.assign",
      "groups.read", "groups.manage", "roles.read"]),
    (uuid.UUID("00000000-0000-0000-0000-000000000c07"), "Audit Reader",
     "Read/export audit evidence -- cannot administer access (NIST AC-5).",
     ["audit.read", "audit.export"]),
    (uuid.UUID("00000000-0000-0000-0000-000000000c08"), "Service Principal",
     "Documented floor for a user account backing an API key or bot integration.",
     ["search.execute", "chat.read", "chat.generate"]),
]


def upgrade() -> None:
    """RBAC-05: seed the pragmatic core role templates (Viewer, Content Manager,
    Workspace Owner/Manager, IAM Admin, Audit Reader, Service Principal) with
    fixed, well-known UUIDs so later tasks can reference them by constant.
    Contributor (00000000-0000-0000-0000-000000000c01) already exists from
    Task 4 (revision c78eddf6863e) and is not re-seeded here."""
    for template_id, name, description, permissions in _TEMPLATES:
        op.execute(
            sa.text(
                "INSERT INTO role_templates (id, created_at, name, description, permissions) "
                "VALUES (:id, now(), :name, :description, :permissions) "
                "ON CONFLICT (name) DO NOTHING"
            ).bindparams(
                sa.bindparam("id", value=template_id, type_=sa.Uuid()),
                sa.bindparam("name", value=name, type_=sa.String()),
                sa.bindparam("description", value=description, type_=sa.String()),
                sa.bindparam(
                    "permissions", value=permissions, type_=sa.ARRAY(sa.String())
                ),
            )
        )


def downgrade() -> None:
    """Downgrade schema."""
    for template_id, _name, _description, _permissions in _TEMPLATES:
        op.execute(
            sa.text("DELETE FROM role_templates WHERE id = :id").bindparams(
                sa.bindparam("id", value=template_id, type_=sa.Uuid())
            )
        )
