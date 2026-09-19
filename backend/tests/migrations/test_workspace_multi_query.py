"""Forward migration coverage for workspaces.multi_query_enabled."""

import asyncio

import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from testcontainers.postgres import PostgresContainer

from ragz.core.db import build_engine, build_session_factory
from tests.migrations._historical import insert_org

_PRE_MIGRATION_REVISION = "9cc2f9645fc0"


def _alembic_config(async_url: str) -> Config:
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", async_url)
    return cfg


async def test_existing_workspace_backfilled_multi_query_disabled() -> None:
    with PostgresContainer("postgres:16-alpine") as pg:
        async_url = pg.get_connection_url().replace("psycopg2", "asyncpg")
        cfg = _alembic_config(async_url)
        await asyncio.to_thread(command.upgrade, cfg, _PRE_MIGRATION_REVISION)

        engine = build_engine(async_url)
        factory = build_session_factory(engine)
        async with factory() as session:
            org_id = await insert_org(session)
            workspace_id = (
                await session.execute(
                    sa.text(
                        "INSERT INTO workspaces (id, created_at, org_id, name, min_score) "
                        "VALUES (gen_random_uuid(), now(), :org_id, 'pre-mq', 0.35) "
                        "RETURNING id"
                    ),
                    {"org_id": org_id},
                )
            ).scalar_one()
            await session.commit()
        await engine.dispose()

        await asyncio.to_thread(command.upgrade, cfg, "head")

        engine = build_engine(async_url)
        factory = build_session_factory(engine)
        async with factory() as session:
            enabled = await session.scalar(
                sa.text(
                    "SELECT multi_query_enabled FROM workspaces WHERE id = :id"
                ),
                {"id": workspace_id},
            )
            assert enabled is False
        await engine.dispose()
