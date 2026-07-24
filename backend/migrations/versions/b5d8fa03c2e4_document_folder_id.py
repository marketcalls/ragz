"""document folder_id

Revision ID: b5d8fa03c2e4
Revises: a4c7e9f2b1d3
Create Date: 2026-07-25 09:30:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'b5d8fa03c2e4'
down_revision: Union[str, Sequence[str], None] = 'a4c7e9f2b1d3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("documents", sa.Column("folder_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_documents_folder_id", "documents", "folders", ["folder_id"], ["id"],
        ondelete="SET NULL",
    )
    op.create_index(op.f("ix_documents_folder_id"), "documents", ["folder_id"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_documents_folder_id"), table_name="documents")
    op.drop_constraint("fk_documents_folder_id", "documents", type_="foreignkey")
    op.drop_column("documents", "folder_id")
