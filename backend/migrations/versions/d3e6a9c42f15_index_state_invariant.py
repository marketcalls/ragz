"""enforce active => fully projected, and reproject stale rows on rollout

Independent audit follow-up to b1c4e7a20d31. Two gaps:

1. Nothing stopped a row being index_state='active' while
   projected_security_revision < security_revision -- exactly the state a
   concurrent ACL update could produce, and one the reconciler's original
   state-only query would skip forever. The invariant is now a CHECK, so the
   database refuses it regardless of application bugs.

2. b1c4e7a20d31 backfilled every existing document to 'active'. For rows at
   rest under the old code that was right, but a document whose Qdrant
   projection had ALREADY failed before the upgrade was frozen in exactly the
   over-grant state the protocol exists to prevent. Restricted documents are
   re-projected on rollout so that assumption is repaired rather than trusted.

Revision ID: d3e6a9c42f15
Revises: c2d5f8b31e42
Create Date: 2026-08-17

"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'd3e6a9c42f15'
down_revision: str | Sequence[str] | None = 'c2d5f8b31e42'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # Any row already violating the invariant is moved to 'pending' rather than
    # failing the migration: pending is the SAFE side (excluded from retrieval)
    # and the reconciler drives it to active within minutes.
    op.execute(
        """
        UPDATE documents SET index_state = 'pending'
        WHERE index_state = 'active'
          AND projected_security_revision <> security_revision
        """
    )
    # Restricted documents carry the payload that actually matters. Marking
    # them pending forces one reconciler pass to re-assert their ACL in Qdrant,
    # which repairs anything stranded by the pre-b1c4e7a20d31 failure mode.
    # Unrestricted documents are left alone: re-projecting the entire corpus
    # would hide every document at once for the duration of the sweep.
    op.execute(
        """
        UPDATE documents SET index_state = 'pending'
        WHERE acl_group_ids IS NOT NULL
        """
    )
    op.create_check_constraint(
        "ck_documents_active_is_projected",
        "documents",
        "index_state <> 'active' OR projected_security_revision = security_revision",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint("ck_documents_active_is_projected", "documents", type_="check")
