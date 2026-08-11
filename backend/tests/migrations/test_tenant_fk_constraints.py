"""RBAC-11: composite tenant-consistency FKs on api_keys/bot_integrations.
Before this migration, `api_keys`/`bot_integrations` had NO foreign keys at
all on `org_id`/`user_id`/`workspace_id`/`created_by` -- nothing in the
database prevented a row from referencing a workspace or user belonging to a
DIFFERENT org than the row's own `org_id`. The migration adds
`UNIQUE(id, org_id)` on `users`/`workspaces` plus composite FKs
`(user_id, org_id) -> users(id, org_id)` and
`(workspace_id, org_id) -> workspaces(id, org_id)` so such a cross-org row is
rejected at INSERT time.

This does NOT reuse the shared `pg_url`/`engine` fixtures from
tests/conftest.py: that `engine` fixture provisions its schema via
`Base.metadata.create_all()`, which bypasses Alembic entirely, so running
`alembic upgrade head` against it fails with "relation already exists". To
genuinely exercise this migration's SQL, this mirrors
tests/migrations/test_seed_contributor_role.py: a dedicated, fresh
PostgresContainer driven through the real Alembic chain, upgraded straight to
head (the constraint under test is enforced going forward, not backfilled --
there is no "pre-migration state" to seed).

`command.upgrade` is dispatched via `asyncio.to_thread` so its internal
`asyncio.run()` runs on its own thread with no event loop already active
(same fix as the sibling migration tests under pytest-asyncio auto mode).
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
from ragz.modules.auth.passwords import hash_password
from ragz.modules.tenancy.models import Organization, Workspace


def _alembic_config(async_url: str) -> Config:
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", async_url)
    return cfg


async def test_api_key_cannot_reference_cross_org_workspace() -> None:
    with PostgresContainer("postgres:16-alpine") as pg:
        async_url = pg.get_connection_url().replace("psycopg2", "asyncpg")
        cfg = _alembic_config(async_url)

        await asyncio.to_thread(command.upgrade, cfg, "head")

        engine = build_engine(async_url)
        factory = build_session_factory(engine)
        async with factory() as session:
            org_a = Organization(name="A")
            org_b = Organization(name="B")
            session.add_all([org_a, org_b])
            await session.flush()
            ws_b = Workspace(org_id=org_b.id, name="WS-B")
            session.add(ws_b)
            await session.flush()
            from ragz.modules.auth.models import User

            user_a = User(
                org_id=org_a.id,
                email="a@x.com",
                password_hash=hash_password("pw123456x"),
                role="user",
            )
            session.add(user_a)
            await session.flush()
            await session.commit()

            # user_a and org_a's own workspace don't exist, but the key
            # references org_a while pointing user_id at user_a (same org,
            # fine) and workspace_id at ws_b -- a workspace that belongs to
            # org_b, not org_a. The composite FK on (workspace_id, org_id)
            # must reject this even though workspace ws_b genuinely exists.
            with pytest.raises(IntegrityError):
                await session.execute(
                    sa.text(
                        "INSERT INTO api_keys (id, created_at, prefix, key_hash, name, org_id, "
                        "user_id, workspace_id, created_by) VALUES "
                        "(:id, now(), 'ragz_sk_xxxx', 'h', 'k', :org_id, :user_id, :ws_id, "
                        ":user_id)"
                    ),
                    {
                        "id": uuid.uuid4(),
                        "org_id": org_a.id,
                        "user_id": user_a.id,
                        "ws_id": ws_b.id,
                    },
                )
                await session.commit()
            await session.rollback()
        await engine.dispose()


async def test_bot_integration_cannot_reference_cross_org_user() -> None:
    with PostgresContainer("postgres:16-alpine") as pg:
        async_url = pg.get_connection_url().replace("psycopg2", "asyncpg")
        cfg = _alembic_config(async_url)

        await asyncio.to_thread(command.upgrade, cfg, "head")

        engine = build_engine(async_url)
        factory = build_session_factory(engine)
        async with factory() as session:
            org_a = Organization(name="A")
            org_b = Organization(name="B")
            session.add_all([org_a, org_b])
            await session.flush()
            ws_a = Workspace(org_id=org_a.id, name="WS-A")
            session.add(ws_a)
            await session.flush()
            from ragz.modules.auth.models import User

            user_b = User(
                org_id=org_b.id,
                email="b@x.com",
                password_hash=hash_password("pw123456x"),
                role="user",
            )
            session.add(user_b)
            await session.flush()
            await session.commit()

            # bot_integration claims org_a, points workspace_id at org_a's
            # own workspace (fine) but user_id at user_b, who belongs to
            # org_b -- the composite FK on (user_id, org_id) must reject it.
            with pytest.raises(IntegrityError):
                await session.execute(
                    sa.text(
                        "INSERT INTO bot_integrations (id, created_at, platform, name, org_id, "
                        "workspace_id, user_id, webhook_id, enabled, created_by) VALUES "
                        "(:id, now(), 'telegram', 'bot', :org_id, :ws_id, :user_id, "
                        ":webhook_id, true, :user_id)"
                    ),
                    {
                        "id": uuid.uuid4(),
                        "org_id": org_a.id,
                        "ws_id": ws_a.id,
                        "user_id": user_b.id,
                        "webhook_id": uuid.uuid4(),
                    },
                )
                await session.commit()
            await session.rollback()
        await engine.dispose()
