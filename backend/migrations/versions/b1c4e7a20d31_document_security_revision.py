"""document security revision (fail-closed ACL projection)

Architecture review 2026-08-17, P0: a security-relevant change commits to
Postgres before it is projected into Qdrant, so an outage in that window leaves
the OLD, broader ACL searchable. These columns make the projection state
explicit so retrieval can refuse to serve a document whose committed security
state has not reached the vector store yet.

  security_revision            monotonically bumped in the SAME commit as any
                               security-relevant change (currently the ACL)
  projected_security_revision  the revision the Qdrant payload actually carries
  index_state                  active | pending | failed -- pending/failed are
                               excluded from retrieval (fail closed)

Backfill: every existing row is treated as already projected (both revisions 0,
state 'active'). That is correct because today's code writes Postgres and Qdrant
in the same call path, so a row at rest is consistent; it is only the failure
window that was unsafe. Backfilling as 'pending' instead would make the entire
corpus unretrievable until a reconciler ran.

Revision ID: b1c4e7a20d31
Revises: a8b9d8757e3c
Create Date: 2026-08-17

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'b1c4e7a20d31'
down_revision: Union[str, Sequence[str], None] = 'a8b9d8757e3c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "documents",
        sa.Column(
            "security_revision", sa.Integer(), nullable=False, server_default="0"
        ),
    )
    op.add_column(
        "documents",
        sa.Column(
            "projected_security_revision",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "documents",
        sa.Column(
            "index_state",
            sa.String(),
            nullable=False,
            server_default="active",
        ),
    )
    op.create_check_constraint(
        "ck_documents_index_state",
        "documents",
        "index_state IN ('active', 'pending', 'failed')",
    )
    # The reconciler and the fail-closed retrieval path both ask the same
    # question: "which documents in this workspace are not projected?". Partial
    # index because the answer is almost always a handful of rows out of the
    # whole corpus.
    op.create_index(
        "ix_documents_unprojected",
        "documents",
        ["workspace_id"],
        unique=False,
        postgresql_where=sa.text("index_state <> 'active'"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_documents_unprojected", table_name="documents")
    op.drop_constraint("ck_documents_index_state", "documents", type_="check")
    op.drop_column("documents", "index_state")
    op.drop_column("documents", "projected_security_revision")
    op.drop_column("documents", "security_revision")
