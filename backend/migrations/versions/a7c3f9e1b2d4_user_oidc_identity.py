"""users.oidc_issuer + users.oidc_subject durable identity binding

Revision ID: a7c3f9e1b2d4
Revises: f1a2b3c4d5e6
Create Date: 2026-08-15 00:00:00.000000

sec RAGZ-PUB-02: OIDC login used to resolve/link accounts by email alone,
which is IdP-controlled and not guaranteed stable or exclusive -- an issuer
able to assert an existing email (or a global email collision) could log the
attacker straight into a victim's account. This migration adds the durable
(issuer, subject) pair the OIDC login flow now requires to resolve an
identity; password-only users keep both columns NULL (nullable columns,
NULL-distinct under Postgres uniqueness semantics, so many NULL/NULL rows
coexist without violating the unique constraint). No backfill is needed --
every existing row starts NULL/NULL, which is exactly the "not yet bound"
state the login flow's mismatch/rebind guard expects.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'a7c3f9e1b2d4'
down_revision: Union[str, Sequence[str], None] = 'f1a2b3c4d5e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("oidc_issuer", sa.String(), nullable=True))
    op.add_column("users", sa.Column("oidc_subject", sa.String(), nullable=True))
    op.create_unique_constraint(
        "uq_users_oidc_identity", "users", ["oidc_issuer", "oidc_subject"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_users_oidc_identity", "users", type_="unique")
    op.drop_column("users", "oidc_subject")
    op.drop_column("users", "oidc_issuer")
