"""Helpers for seeding rows at a HISTORICAL Alembic revision.

Migration tests upgrade to the revision just before the one under test, seed
pre-existing data, then upgrade to head to prove the forward migration handles
real rows. The trap: seeding with the live ORM models binds those inserts to
TODAY's schema, so any later migration that adds a column breaks tests for
migrations that shipped long before it. `organizations.contact_email` (added by
a8b9d8757e3c, "org profile fields") did exactly that -- three migration tests
started failing with UndefinedColumnError on a column their revision predates,
even though the migrations they cover were unchanged.

Seed with explicit SQL naming only columns that existed at that revision. It is
more verbose than the ORM and that is the point: the column list is pinned in
the test, so a future migration cannot silently rewrite the past.
"""

from __future__ import annotations

from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession


async def insert_org(session: AsyncSession, name: str = "PreMigrationOrg") -> UUID:
    """Insert an organization using only its original three columns.

    id/created_at/name have been present since the initial revision, so this is
    valid at every revision these tests target. Deliberately NOT
    `Organization(...)`: the ORM would also send whatever columns the model has
    grown since.
    """
    row = await session.execute(
        sa.text(
            "INSERT INTO organizations (id, created_at, name) "
            "VALUES (gen_random_uuid(), now(), :name) RETURNING id"
        ),
        {"name": name},
    )
    return row.scalar_one()  # type: ignore[no-any-return]
