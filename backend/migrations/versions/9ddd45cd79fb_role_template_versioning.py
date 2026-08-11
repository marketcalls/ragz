"""role template versioning

Revision ID: 9ddd45cd79fb
Revises: 9373556f189d
Create Date: 2026-08-11 01:29:14.175801

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '9ddd45cd79fb'
down_revision: Union[str, Sequence[str], None] = '9373556f189d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # RBAC-09: role templates get a lifecycle (draft|active|archived) plus a
    # monotonic version stamp. server_default="active" here means EVERY
    # existing template (the 8 seeded core/Contributor templates from Task 8
    # plus any org-authored custom template already in production) stays
    # immediately assignable after this migration -- only NEW templates
    # created after this task ships start as "draft" (enforced in
    # application code, not by this column default; see RoleTemplate.status
    # in models.py, which defaults to "draft").
    op.add_column("role_templates", sa.Column("status", sa.String(), nullable=False,
                                                server_default="active"))
    op.add_column("role_templates", sa.Column("version", sa.Integer(), nullable=False,
                                                server_default="1"))
    op.create_check_constraint(
        "ck_role_templates_status", "role_templates",
        "status IN ('draft', 'active', 'archived')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_role_templates_status", "role_templates", type_="check")
    op.drop_column("role_templates", "version")
    op.drop_column("role_templates", "status")
