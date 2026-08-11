"""audit event fields

Revision ID: 9b3f7c2a1e40
Revises: 0521b696bbe9
Create Date: 2026-08-11 00:00:00.000000

"""
from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = '9b3f7c2a1e40'
down_revision: Union[str, Sequence[str], None] = '0521b696bbe9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("audit_events", sa.Column("result", sa.String(), nullable=False,
                                            server_default="success"))
    op.add_column("audit_events", sa.Column("reason_code", sa.String(), nullable=True))
    op.add_column("audit_events", sa.Column("request_id", sa.String(), nullable=True))
    op.add_column("audit_events", sa.Column("source_ip", sa.String(), nullable=True))
    op.add_column("audit_events", sa.Column("auth_method", sa.String(), nullable=True))
    op.add_column("audit_events", sa.Column("credential_id", sa.String(), nullable=True))
    op.create_check_constraint(
        "ck_audit_events_result", "audit_events", "result IN ('success', 'denied')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_audit_events_result", "audit_events", type_="check")
    op.drop_column("audit_events", "credential_id")
    op.drop_column("audit_events", "auth_method")
    op.drop_column("audit_events", "source_ip")
    op.drop_column("audit_events", "request_id")
    op.drop_column("audit_events", "reason_code")
    op.drop_column("audit_events", "result")
