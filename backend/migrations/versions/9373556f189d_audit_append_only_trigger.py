"""audit append only trigger

Revision ID: 9373556f189d
Revises: 9b3f7c2a1e40
Create Date: 2026-08-11 00:00:00.000000

"""
from collections.abc import Sequence
from typing import Union

from alembic import op

revision: str = '9373556f189d'
down_revision: Union[str, Sequence[str], None] = '9b3f7c2a1e40'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """RBAC-07: audit_events is append-only. A BEFORE UPDATE/DELETE trigger
    enforces this at the database layer for every role, including the table
    owner, unless the trigger is explicitly dropped (a conspicuous DDL
    change, not an ordinary application write). True DB-role separation
    (a dedicated non-owner writer role) is a deployment-topology change
    tracked separately -- see the plan's Deferred section."""
    op.execute(
        """
        CREATE OR REPLACE FUNCTION audit_events_append_only() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'audit_events is append-only: % is not permitted', TG_OP;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER audit_events_no_update
            BEFORE UPDATE ON audit_events
            FOR EACH ROW EXECUTE FUNCTION audit_events_append_only();
        """
    )
    op.execute(
        """
        CREATE TRIGGER audit_events_no_delete
            BEFORE DELETE ON audit_events
            FOR EACH ROW EXECUTE FUNCTION audit_events_append_only();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS audit_events_no_delete ON audit_events;")
    op.execute("DROP TRIGGER IF EXISTS audit_events_no_update ON audit_events;")
    op.execute("DROP FUNCTION IF EXISTS audit_events_append_only();")
