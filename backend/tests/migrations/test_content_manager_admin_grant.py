"""RBAC-05: the paired forward migration that grants every EXISTING
admin/superadmin the seeded "Content Manager" template (fixed id ...c03) so
nobody loses today's unrestricted content access when admin's automatic
content-ACL bypass is removed in the same commit.

This does NOT reuse the shared `pg_url`/`engine` fixtures from
tests/conftest.py: that `engine` fixture provisions its schema via
`Base.metadata.create_all()`, which bypasses Alembic entirely, so running
`alembic upgrade head` against it fails with "relation already exists". To
genuinely exercise this migration's SQL, this mirrors
tests/migrations/test_seed_contributor_role.py: a dedicated, fresh
PostgresContainer driven through the real Alembic chain -- upgraded first to
the revision just before this one (Content Manager template c03 already
seeded, but no admin grant yet), seeded with a pre-migration admin whose
custom_role_id IS NULL, then upgraded to head.

`command.upgrade` is dispatched via `asyncio.to_thread` so its internal
`asyncio.run()` runs on its own thread with no event loop already active
(same fix as the sibling migration tests under pytest-asyncio auto mode).
"""

import asyncio
import uuid

import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from testcontainers.postgres import PostgresContainer

from ragz.core.db import build_engine, build_session_factory
from ragz.modules.auth.passwords import hash_password
from tests.migrations._historical import insert_org

# Revision that seeds the core role templates (incl. Content Manager c03),
# i.e. the schema state immediately BEFORE this admin-grant migration.
_PRE_MIGRATION_REVISION = "d4a26ef9adfe"
_CONTENT_MANAGER_ID = uuid.UUID("00000000-0000-0000-0000-000000000c03")


def _alembic_config(async_url: str) -> Config:
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", async_url)
    return cfg


async def test_existing_admin_and_superadmin_granted_content_manager() -> None:
    with PostgresContainer("postgres:16-alpine") as pg:
        async_url = pg.get_connection_url().replace("psycopg2", "asyncpg")
        cfg = _alembic_config(async_url)

        # Schema as it existed immediately before this migration (c03 seeded).
        await asyncio.to_thread(command.upgrade, cfg, _PRE_MIGRATION_REVISION)

        engine = build_engine(async_url)
        factory = build_session_factory(engine)
        admin_id = uuid.uuid4()
        superadmin_id = uuid.uuid4()
        already_id = uuid.uuid4()
        user_id = uuid.uuid4()
        other_template_id = uuid.uuid4()
        async with factory() as session:
            # Raw insert, not the ORM: see tests/migrations/_historical.py --
            # the ORM sends columns this revision does not have yet.
            org_id = await insert_org(session)
            # A distinct pre-existing role template an admin might already hold.
            await session.execute(
                sa.text(
                    "INSERT INTO role_templates (id, created_at, name, description, permissions) "
                    "VALUES (:id, now(), :name, :description, :permissions)"
                ).bindparams(
                    sa.bindparam("id", value=other_template_id, type_=sa.Uuid()),
                    sa.bindparam("name", value="Pre-existing", type_=sa.String()),
                    sa.bindparam("description", value="pre-existing grant", type_=sa.String()),
                    sa.bindparam(
                        "permissions", value=["documents.list"], type_=sa.ARRAY(sa.String())
                    ),
                )
            )
            rows = [
                (admin_id, "admin@x.com", "admin", None),
                (superadmin_id, "root@x.com", "superadmin", None),
                (already_id, "kept@x.com", "admin", other_template_id),
                (user_id, "plain@x.com", "user", None),
            ]
            for uid, email, role, crid in rows:
                await session.execute(
                    sa.text(
                        "INSERT INTO users (id, created_at, org_id, email, password_hash, "
                        "role, active, custom_role_id) VALUES (:id, now(), :org_id, :email, "
                        ":ph, :role, true, :crid)"
                    ).bindparams(
                        sa.bindparam("id", value=uid, type_=sa.Uuid()),
                        sa.bindparam("org_id", value=org_id, type_=sa.Uuid()),
                        sa.bindparam("email", value=email, type_=sa.String()),
                        sa.bindparam("ph", value=hash_password("pw123456x"), type_=sa.String()),
                        sa.bindparam("role", value=role, type_=sa.String()),
                        sa.bindparam("crid", value=crid, type_=sa.Uuid()),
                    )
                )
            await session.commit()
        await engine.dispose()

        await asyncio.to_thread(command.upgrade, cfg, "head")

        engine = build_engine(async_url)
        factory = build_session_factory(engine)
        result: dict[uuid.UUID, uuid.UUID | None] = {}
        async with factory() as session:
            for uid in (admin_id, superadmin_id, already_id, user_id):
                result[uid] = (
                    await session.execute(
                        sa.text("SELECT custom_role_id FROM users WHERE id = :id"),
                        {"id": uid},
                    )
                ).scalar_one()
        await engine.dispose()

        # Admin & superadmin with no prior grant now carry Content Manager.
        assert result[admin_id] == _CONTENT_MANAGER_ID
        assert result[superadmin_id] == _CONTENT_MANAGER_ID
        # An admin who already held a different template is left untouched.
        assert result[already_id] == other_template_id
        # A plain 'user' (already Contributor from RBAC-04, or NULL) is untouched
        # by THIS migration -- it only targets admin/superadmin rows.
        assert result[user_id] != _CONTENT_MANAGER_ID
