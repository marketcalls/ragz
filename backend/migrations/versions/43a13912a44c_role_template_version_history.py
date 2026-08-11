"""role template version history

Revision ID: 43a13912a44c
Revises: 9ddd45cd79fb
Create Date: 2026-08-11 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = '43a13912a44c'
down_revision: Union[str, Sequence[str], None] = '9ddd45cd79fb'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "role_template_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("role_template_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("permissions", postgresql.ARRAY(sa.String()), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["role_template_id"], ["role_templates.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("role_template_id", "version", name="uq_role_template_versions"),
    )
    op.create_index(
        op.f("ix_role_template_versions_role_template_id"), "role_template_versions",
        ["role_template_id"], unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_role_template_versions_role_template_id"), table_name="role_template_versions"
    )
    op.drop_table("role_template_versions")
