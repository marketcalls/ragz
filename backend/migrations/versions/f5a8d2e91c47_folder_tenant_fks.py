"""composite same-tenant foreign keys for folder references

Cubic P1 follow-up to e4f7c1a83b26, which paired workspace_id and created_by
with org_id but left the FOLDER references single-column:

  documents.folder_id       -> folders.id
  folders.parent_folder_id  -> folders.id

So a bypassed write could still file org A's document under org B's folder, and
a parent-folder delete could cascade into another tenant's subtree. Pairing each
with org_id closes both.

Requires UNIQUE(id, org_id) on folders as the composite target, mirroring what
fe047b5cdf08 did for users and workspaces.

Both columns are NULLABLE (NULL = root level / no folder). Under MATCH SIMPLE
the constraint is skipped for those rows and enforced for every row that names a
folder -- which is exactly right, not a gap.

The existing single-column FKs are REPLACED rather than kept alongside: they
carry the ondelete behaviour (SET NULL for documents, CASCADE for the folder
tree), which must move to the composite constraint or it would be lost.

Revision ID: f5a8d2e91c47
Revises: e4f7c1a83b26
Create Date: 2026-08-17

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'f5a8d2e91c47'
down_revision: Union[str, Sequence[str], None] = 'e4f7c1a83b26'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_unique_constraint("uq_folders_id_org_id", "folders", ["id", "org_id"])

    op.drop_constraint("fk_documents_folder_id", "documents", type_="foreignkey")
    op.create_foreign_key(
        "fk_documents_folder_id_org", "documents", "folders",
        ["folder_id", "org_id"], ["id", "org_id"],
        # SET NULL (folder_id), NOT a bare SET NULL: a bare SET NULL nulls EVERY
        # column of the FK, and org_id is NOT NULL -- so deleting any folder
        # aborted with a not-null violation instead of orphaning its documents
        # to the root. The column list (Postgres 15+) restricts the nulling to
        # the folder reference and leaves the tenant column intact.
        ondelete="SET NULL (folder_id)",
    )

    op.drop_constraint(
        "folders_parent_folder_id_fkey", "folders", type_="foreignkey"
    )
    op.create_foreign_key(
        "fk_folders_parent_folder_id_org", "folders", "folders",
        ["parent_folder_id", "org_id"], ["id", "org_id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint("fk_folders_parent_folder_id_org", "folders", type_="foreignkey")
    op.create_foreign_key(
        "folders_parent_folder_id_fkey", "folders", "folders",
        ["parent_folder_id"], ["id"], ondelete="CASCADE",
    )
    op.drop_constraint("fk_documents_folder_id_org", "documents", type_="foreignkey")
    op.create_foreign_key(
        "fk_documents_folder_id", "documents", "folders",
        ["folder_id"], ["id"], ondelete="SET NULL",
    )
    op.drop_constraint("uq_folders_id_org_id", "folders", type_="unique")
