"""document deletion actor

reconcile_stuck_documents republishes documents.delete for rows stranded
mid-delete, and it used doc.created_by as the actor because nothing recorded who
had actually asked. The creator is frequently not the deleter, so the replayed
document.deleted audit named an unrelated user -- a false record in an
append-only log (Cubic P2).

documents.deleted_by is written WITH the flip to status="deleting" at both
transition sites (the single-document route and the folder cascade), so the
reconciler can name the real requester.

Deliberately NOT backfilled. Rows that entered "deleting" before this column
existed have no recoverable actor, and inventing one -- created_by, or the
migration runner -- would bake the very falsehood this fixes into history. They
stay NULL, and run_delete/record_audit already accept a null actor.

Revision ID: 0bf293309b11
Revises: 81d69f832405
Create Date: 2026-08-18

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '0bf293309b11'
down_revision: Union[str, Sequence[str], None] = '81d69f832405'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("documents", sa.Column("deleted_by", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_documents_deleted_by", "documents", "users", ["deleted_by"], ["id"]
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint("fk_documents_deleted_by", "documents", type_="foreignkey")
    op.drop_column("documents", "deleted_by")
