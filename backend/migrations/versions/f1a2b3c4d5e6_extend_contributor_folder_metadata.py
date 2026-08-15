"""extend contributor role with granular folder + document-metadata actions

Revision ID: f1a2b3c4d5e6
Revises: b45b119d8e97
Create Date: 2026-08-15 00:00:00.000000

sec RAGZ-PUB-01 follow-on: the folder CRUD routes and the per-document
metadata-value PUT used to be (incorrectly) gated on documents.upload /
documents.delete rather than on the granular actions they DECLARE in
api/policy.py (folders.create/read/update/delete, documents.metadata.update).
Aligning enforcement to the declared action means a role must now hold those
granular actions to reach the routes. The seeded "Contributor" role
(c78eddf6863e) -- the migration baseline that preserves every pre-RBAC-04
role='user' account's capability -- reached those routes via its
documents.upload/documents.delete grants, so it must gain the granular actions
too or every existing contributor silently loses folder management and
document-metadata editing. Metadata FIELD (schema) management stays out: it was
gated on workspace.configure (admin-only), which Contributor never held.
"""
import uuid
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'f1a2b3c4d5e6'
down_revision: Union[str, Sequence[str], None] = 'b45b119d8e97'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_CONTRIBUTOR_ID = uuid.UUID("00000000-0000-0000-0000-000000000c01")
# Granular actions the Contributor role previously reached via documents.upload
# (folder create/rename/move, metadata-value PUT) and documents.delete (folder
# delete/preview), plus the folder listing that was auth-only before.
_ADDED_PERMISSIONS = [
    "folders.create", "folders.read", "folders.update", "folders.delete",
    "documents.metadata.update",
]


def upgrade() -> None:
    # Idempotent union-append: add only the actions not already present, so
    # re-running (or a partially-updated row) never duplicates entries.
    op.execute(
        sa.text(
            "UPDATE role_templates "
            "SET permissions = ARRAY("
            "  SELECT DISTINCT unnest(permissions || :added)"
            ") "
            "WHERE id = :id"
        ).bindparams(
            sa.bindparam("id", value=_CONTRIBUTOR_ID, type_=sa.Uuid()),
            sa.bindparam("added", value=_ADDED_PERMISSIONS, type_=sa.ARRAY(sa.String())),
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE role_templates "
            "SET permissions = ARRAY("
            "  SELECT p FROM unnest(permissions) AS p WHERE p <> ALL(:added)"
            ") "
            "WHERE id = :id"
        ).bindparams(
            sa.bindparam("id", value=_CONTRIBUTOR_ID, type_=sa.Uuid()),
            sa.bindparam("added", value=_ADDED_PERMISSIONS, type_=sa.ARRAY(sa.String())),
        )
    )
