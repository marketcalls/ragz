"""RBAC-11: CHECK constraints on `users.role`, `invitations.role`, and
`documents.status` so a typo (e.g. writing "delted" instead of "deleting")
fails loudly at INSERT time instead of silently creating a row with an
unfilterable status/role that no code path recognizes.

This does NOT reuse the shared `pg_url`/`engine` fixtures from
tests/conftest.py: that `engine` fixture provisions its schema via
`Base.metadata.create_all()`, which bypasses Alembic entirely, so running
`alembic upgrade head` against it fails with "relation already exists". To
genuinely exercise this migration's SQL, this mirrors
tests/migrations/test_tenant_fk_constraints.py: a dedicated, fresh
PostgresContainer driven through the real Alembic chain, upgraded straight to
head (the constraint under test is enforced going forward, not backfilled --
there is no "pre-migration state" to seed).

`command.upgrade` is dispatched via `asyncio.to_thread` so its internal
`asyncio.run()` runs on its own thread with no event loop already active
(same fix as the sibling migration tests under pytest-asyncio auto mode) --
calling it directly from this async test would raise "asyncio.run() cannot
be called from a running event loop".
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


async def test_user_role_rejects_unknown_value() -> None:
    with PostgresContainer("postgres:16-alpine") as pg:
        async_url = pg.get_connection_url().replace("psycopg2", "asyncpg")
        cfg = _alembic_config(async_url)

        await asyncio.to_thread(command.upgrade, cfg, "head")

        engine = build_engine(async_url)
        factory = build_session_factory(engine)
        async with factory() as session:
            org = Organization(name="EnumTest")
            session.add(org)
            await session.flush()
            await session.commit()
            with pytest.raises(IntegrityError):
                await session.execute(
                    sa.text(
                        "INSERT INTO users (id, created_at, org_id, email, password_hash, role, "
                        "active) VALUES (:id, now(), :org_id, 'x@x.com', 'h', "
                        "'not_a_real_role', true)"
                    ),
                    {"id": uuid.uuid4(), "org_id": org.id},
                )
                await session.commit()
            await session.rollback()
        await engine.dispose()


async def test_invitation_role_rejects_unknown_value() -> None:
    with PostgresContainer("postgres:16-alpine") as pg:
        async_url = pg.get_connection_url().replace("psycopg2", "asyncpg")
        cfg = _alembic_config(async_url)

        await asyncio.to_thread(command.upgrade, cfg, "head")

        engine = build_engine(async_url)
        factory = build_session_factory(engine)
        async with factory() as session:
            org = Organization(name="EnumTest")
            session.add(org)
            await session.flush()
            await session.commit()
            with pytest.raises(IntegrityError):
                await session.execute(
                    sa.text(
                        "INSERT INTO invitations (id, created_at, org_id, email, role, "
                        "token_hash, expires_at) VALUES (:id, now(), :org_id, 'x@x.com', "
                        "'superadmin', 'th', now() + interval '1 day')"
                    ),
                    {"id": uuid.uuid4(), "org_id": org.id},
                )
                await session.commit()
            await session.rollback()
        await engine.dispose()


async def test_document_status_rejects_unknown_value() -> None:
    with PostgresContainer("postgres:16-alpine") as pg:
        async_url = pg.get_connection_url().replace("psycopg2", "asyncpg")
        cfg = _alembic_config(async_url)

        await asyncio.to_thread(command.upgrade, cfg, "head")

        engine = build_engine(async_url)
        factory = build_session_factory(engine)
        async with factory() as session:
            org = Organization(name="EnumTest")
            session.add(org)
            await session.flush()
            ws = Workspace(org_id=org.id, name="WS")
            session.add(ws)
            await session.flush()
            from ragz.modules.auth.models import User

            user = User(
                org_id=org.id,
                email="doc-owner@x.com",
                password_hash=hash_password("pw123456x"),
                role="user",
            )
            session.add(user)
            await session.flush()
            await session.commit()

            doc_id = uuid.uuid4()
            # Every NOT-NULL column without a server_default must be supplied
            # explicitly (version/updated_at/is_current/approved/
            # vectors_present) -- otherwise the raw INSERT would fail on a
            # NOT NULL violation before ever reaching the status CHECK,
            # making the test pass for the wrong reason.
            with pytest.raises(IntegrityError):
                await session.execute(
                    sa.text(
                        "INSERT INTO documents (id, created_at, org_id, workspace_id, filename, "
                        "mime, size_bytes, content_hash, status, storage_key, created_by, "
                        "lineage_id, updated_at, version, is_current, approved, "
                        "vectors_present) VALUES (:id, now(), :org_id, :ws_id, 'f.pdf', "
                        "'application/pdf', 10, 'h', 'not_a_real_status', 'k', :user_id, :id, "
                        "now(), 1, true, false, false)"
                    ),
                    {
                        "id": doc_id,
                        "org_id": org.id,
                        "ws_id": ws.id,
                        "user_id": user.id,
                    },
                )
                await session.commit()
            await session.rollback()
        await engine.dispose()
