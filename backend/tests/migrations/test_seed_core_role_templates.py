"""RBAC-05: the forward migration that seeds the pragmatic core role
templates (Viewer, Content Manager, Workspace Owner/Manager, IAM Admin,
Audit Reader, Service Principal) with fixed, well-known UUIDs.

This does NOT reuse the shared `pg_url`/`engine` fixtures from
tests/conftest.py: that `engine` fixture provisions its schema via
`Base.metadata.create_all()`, which creates every table directly from the
current ORM metadata, bypassing Alembic entirely. Running `alembic upgrade
head` against a database whose tables already exist that way fails
immediately with "relation already exists". To genuinely exercise this
migration's SQL, this test mirrors
tests/migrations/test_seed_contributor_role.py's pattern instead: a
dedicated, fresh PostgresContainer driven through the real Alembic chain,
upgraded straight to head.

Alembic's env.py drives migrations through an async engine internally via
its own `asyncio.run(...)` (see migrations/env.py). This test is itself an
async test (pytest-asyncio, `asyncio_mode = "auto"`), so calling
`command.upgrade` directly would raise "asyncio.run() cannot be called from
a running event loop". Mirroring the same fix used in
test_seed_contributor_role.py, `command.upgrade` is dispatched via
`asyncio.to_thread` so its internal `asyncio.run()` runs on its own thread
with no event loop already active.
"""

import asyncio

import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from testcontainers.postgres import PostgresContainer

from ragz.core.db import build_engine, build_session_factory


def _alembic_config(async_url: str) -> Config:
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", async_url)
    return cfg


async def test_core_role_templates_seeded() -> None:
    with PostgresContainer("postgres:16-alpine") as pg:
        async_url = pg.get_connection_url().replace("psycopg2", "asyncpg")
        cfg = _alembic_config(async_url)

        await asyncio.to_thread(command.upgrade, cfg, "head")

        engine = build_engine(async_url)
        factory = build_session_factory(engine)
        async with factory() as session:
            rows = (
                await session.execute(sa.text("SELECT name, permissions FROM role_templates"))
            ).all()
        await engine.dispose()

        names = {r[0] for r in rows}
        expected = {
            "Contributor", "Viewer", "Content Manager", "Workspace Owner",
            "Workspace Manager", "IAM Admin", "Audit Reader", "Service Principal",
        }
        assert expected <= names

        by_name = {r[0]: r[1] for r in rows}
        assert "documents.acl.bypass" in by_name["Content Manager"]
        assert by_name["Audit Reader"] == ["audit.read", "audit.export"]
