"""RBAC-04: the forward migration that seeds a "Contributor" role template
and assigns every pre-existing role='user' account to it, so nobody's access
regresses when DEFAULT_USER_PERMISSIONS later narrows to read-only.

This does NOT reuse the shared `pg_url`/`engine` fixtures from
tests/conftest.py: that `engine` fixture provisions its schema via
`Base.metadata.create_all()`, which creates every table (including
`role_templates`/`users`) directly from the current ORM metadata, bypassing
Alembic entirely. Running `alembic upgrade head` against a database whose
tables already exist that way fails immediately with "relation already
exists". To genuinely exercise this migration's SQL, this test mirrors
tests/test_migrations.py's pattern instead: a dedicated, fresh
PostgresContainer driven through the real Alembic chain, upgraded first to
the revision just before this one (to get a "pre-migration" schema with no
Contributor role yet), seeded with a legacy role='user' account, then
upgraded to head.

Alembic's env.py drives migrations through an async engine internally via its
own `asyncio.run(...)` (see migrations/env.py). This test is itself an async
test (pytest-asyncio, `asyncio_mode = "auto"`), so calling `command.upgrade`
directly would raise "asyncio.run() cannot be called from a running event
loop". Mirroring tests/worker/test_tasks.py's documented fix for the same
class of collision, each `command.upgrade` call is dispatched via
`asyncio.to_thread` so its internal `asyncio.run()` runs on its own thread
with no event loop already active.
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

_PRE_MIGRATION_REVISION = "4380e51fba67"


def _alembic_config(async_url: str) -> Config:
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", async_url)
    return cfg


async def test_contributor_seeded_and_assigned_to_existing_users() -> None:
    with PostgresContainer("postgres:16-alpine") as pg:
        async_url = pg.get_connection_url().replace("psycopg2", "asyncpg")
        cfg = _alembic_config(async_url)

        # Schema as it existed immediately before this migration.
        await asyncio.to_thread(command.upgrade, cfg, _PRE_MIGRATION_REVISION)

        engine = build_engine(async_url)
        factory = build_session_factory(engine)
        pre_user_id = uuid.uuid4()
        async with factory() as session:
            # Raw insert, not the ORM: see tests/migrations/_historical.py --
            # the ORM sends columns this revision does not have yet.
            org_id = await insert_org(session)
            await session.execute(
                sa.text(
                    "INSERT INTO users (id, created_at, org_id, email, password_hash, role, "
                    "active, custom_role_id) VALUES (:id, now(), :org_id, :email, :ph, 'user', "
                    "true, NULL)"
                ),
                {
                    "id": pre_user_id,
                    "org_id": org_id,
                    "email": "pre@x.com",
                    "ph": hash_password("pw123456x"),
                },
            )
            await session.commit()
        await engine.dispose()

        await asyncio.to_thread(command.upgrade, cfg, "head")

        engine = build_engine(async_url)
        factory = build_session_factory(engine)
        async with factory() as session:
            row = (
                await session.execute(
                    sa.text("SELECT custom_role_id FROM users WHERE id = :id"),
                    {"id": pre_user_id},
                )
            ).first()
            assert row is not None and row[0] is not None
            template = (
                await session.execute(
                    sa.text("SELECT name, permissions FROM role_templates WHERE id = :id"),
                    {"id": row[0]},
                )
            ).first()
            assert template is not None
            assert template[0] == "Contributor"
            perms = set(template[1])
            assert {"documents.upload", "documents.delete", "chat.generate"} <= perms
            # No-regression invariant: assigning Contributor at migration time
            # must never grant an existing user LESS than the fallback default
            # they had before. In particular chat.use is still a live runtime
            # gate on the chat routes, so it MUST be present or every migrated
            # user loses chat access. Importing here (not at module top) keeps
            # this a behavioral check against the code's current default set.
            from ragz.modules.tenancy.permissions import DEFAULT_USER_PERMISSIONS

            assert DEFAULT_USER_PERMISSIONS <= perms, (
                "Contributor must be a superset of DEFAULT_USER_PERMISSIONS; "
                f"missing: {sorted(DEFAULT_USER_PERMISSIONS - perms)}"
            )
            assert "chat.use" in perms
        await engine.dispose()
