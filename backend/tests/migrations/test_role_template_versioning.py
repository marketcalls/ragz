"""RBAC-09: role_templates gains a `status` (draft|active|archived) lifecycle
column and a monotonic `version` column. server_default="active" on `status`
means every EXISTING row (seeded core templates, org-authored custom
templates already in production) stays immediately assignable after this
migration ships -- only new, application-created templates start "draft"
(enforced in ORM/service code, not by this column default).

Mirrors tests/migrations/test_role_status_enums.py: a dedicated, fresh
PostgresContainer driven through the real Alembic chain to head, since the
shared `engine` fixture in tests/conftest.py provisions its schema via
Base.metadata.create_all() (bypassing Alembic), which would make
`alembic upgrade head` fail with "relation already exists".
"""

import asyncio
import uuid

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.exc import IntegrityError
from testcontainers.postgres import PostgresContainer

from ragz.core.db import build_engine, build_session_factory
from ragz.modules.tenancy.models import RoleTemplate


def _alembic_config(async_url: str) -> Config:
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", async_url)
    return cfg


async def test_existing_row_backfills_to_active_status_and_version_one() -> None:
    """A row inserted with no explicit status/version (simulating a
    pre-migration row) gets the server_default -- "active"/1 -- so it stays
    immediately assignable."""
    with PostgresContainer("postgres:16-alpine") as pg:
        async_url = pg.get_connection_url().replace("psycopg2", "asyncpg")
        cfg = _alembic_config(async_url)

        await asyncio.to_thread(command.upgrade, cfg, "head")

        engine = build_engine(async_url)
        factory = build_session_factory(engine)
        async with factory() as session:
            template_id = uuid.uuid4()
            await session.execute(
                sa.text(
                    "INSERT INTO role_templates (id, created_at, name, description, "
                    "permissions) VALUES (:id, now(), 'Pre-Migration Template', '', "
                    "ARRAY['chat.read'])"
                ),
                {"id": template_id},
            )
            await session.commit()

            row = (
                await session.execute(
                    sa.text("SELECT status, version FROM role_templates WHERE id = :id"),
                    {"id": template_id},
                )
            ).one()
            assert row.status == "active"
            assert row.version == 1
        await engine.dispose()


async def test_status_check_constraint_rejects_unknown_value() -> None:
    with PostgresContainer("postgres:16-alpine") as pg:
        async_url = pg.get_connection_url().replace("psycopg2", "asyncpg")
        cfg = _alembic_config(async_url)

        await asyncio.to_thread(command.upgrade, cfg, "head")

        engine = build_engine(async_url)
        factory = build_session_factory(engine)
        async with factory() as session:
            with pytest.raises(IntegrityError):
                await session.execute(
                    sa.text(
                        "INSERT INTO role_templates (id, created_at, name, description, "
                        "permissions, status, version) VALUES (:id, now(), 'Bad Status', '', "
                        "ARRAY['chat.read'], 'not_a_real_status', 1)"
                    ),
                    {"id": uuid.uuid4()},
                )
                await session.commit()
            await session.rollback()
        await engine.dispose()


async def test_model_default_for_new_row_is_draft_version_one() -> None:
    """The ORM-level default (RoleTemplate.status="draft") is deliberately
    asymmetric with the migration's server_default="active" -- new
    application-created templates start draft; existing rows stay active."""
    with PostgresContainer("postgres:16-alpine") as pg:
        async_url = pg.get_connection_url().replace("psycopg2", "asyncpg")
        cfg = _alembic_config(async_url)

        await asyncio.to_thread(command.upgrade, cfg, "head")

        engine = build_engine(async_url)
        factory = build_session_factory(engine)
        async with factory() as session:
            template = RoleTemplate(name="New Template", permissions=["chat.read"])
            session.add(template)
            await session.commit()
            await session.refresh(template)
            assert template.status == "draft"
            assert template.version == 1
        await engine.dispose()
