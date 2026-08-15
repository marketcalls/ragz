"""extend contributor role with reports.view.self

Revision ID: e3b9f7a1c6d2
Revises: d7f2a9c4e1b8
Create Date: 2026-08-16 00:00:00.000000

Cost-reporting Phase 2b: `reports.view.self` (the scoped-reporting floor --
every member may see their OWN usage/cost) joins DEFAULT_USER_PERMISSIONS, and
`GET /reports/usage[/export]` gates the self scope on it via require_action.
The seeded "Contributor" role (c78eddf6863e) -- the migration baseline that
preserves every pre-RBAC-04 role='user' account's capability -- must stay a
strict superset of DEFAULT_USER_PERMISSIONS, so it gains this action too or
every existing contributor silently loses access to its own usage report.
"""
import uuid
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'e3b9f7a1c6d2'
down_revision: Union[str, Sequence[str], None] = 'd7f2a9c4e1b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_CONTRIBUTOR_ID = uuid.UUID("00000000-0000-0000-0000-000000000c01")
_ADDED_PERMISSIONS = ["reports.view.self"]


def upgrade() -> None:
    # Idempotent union-append (mirrors f1a2b3c4d5e6): add only actions not
    # already present, so re-running never duplicates entries.
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
