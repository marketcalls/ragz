"""folders table

Revision ID: a4c7e9f2b1d3
Revises: f3a0b6c4d8e5
Create Date: 2026-07-25 09:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'a4c7e9f2b1d3'
down_revision: Union[str, Sequence[str], None] = 'f3a0b6c4d8e5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "folders",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("parent_folder_id", sa.Uuid(), nullable=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"]),
        sa.ForeignKeyConstraint(["parent_folder_id"], ["folders.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id", "parent_folder_id", "name",
            name="uq_folders_workspace_parent_name",
        ),
    )
    op.create_index(op.f("ix_folders_org_id"), "folders", ["org_id"], unique=False)
    op.create_index(
        op.f("ix_folders_workspace_id"), "folders", ["workspace_id"], unique=False
    )
    op.create_index(
        op.f("ix_folders_parent_folder_id"), "folders", ["parent_folder_id"], unique=False
    )
    op.create_index(
        "uq_folders_workspace_root_name", "folders", ["workspace_id", "name"],
        unique=True, postgresql_where=sa.text("parent_folder_id IS NULL"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("uq_folders_workspace_root_name", table_name="folders")
    op.drop_index(op.f("ix_folders_parent_folder_id"), table_name="folders")
    op.drop_index(op.f("ix_folders_workspace_id"), table_name="folders")
    op.drop_index(op.f("ix_folders_org_id"), table_name="folders")
    op.drop_table("folders")
