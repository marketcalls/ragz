"""RBAC-07: audit_events is append-only, enforced by a Postgres
`BEFORE UPDATE OR DELETE` row-level trigger that always raises -- for EVERY
role, including the table owner, unless the trigger is explicitly dropped (a
conspicuous DDL action distinct from an ordinary application UPDATE/DELETE).

This does NOT reuse the shared `pg_url`/`engine` fixtures from
tests/conftest.py: that `engine` fixture provisions its schema via
`Base.metadata.create_all()`, which bypasses Alembic entirely, so running
`alembic upgrade head` against it fails with "relation already exists". To
genuinely exercise this migration's SQL, this mirrors
tests/migrations/test_tenant_fk_constraints.py: a dedicated, fresh
PostgresContainer driven through the real Alembic chain, upgraded straight to
head.

`command.upgrade` is dispatched via `asyncio.to_thread` so its internal
`asyncio.run()` runs on its own thread with no event loop already active
(same fix as the sibling migration tests under pytest-asyncio auto mode).
"""

import asyncio

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.exc import DBAPIError, IntegrityError
from testcontainers.postgres import PostgresContainer

from ragz.core.db import build_engine, build_session_factory
from ragz.modules.audit.models import AuditEvent


def _alembic_config(async_url: str) -> Config:
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", async_url)
    return cfg


async def test_audit_event_cannot_be_updated_or_deleted() -> None:
    with PostgresContainer("postgres:16-alpine") as pg:
        async_url = pg.get_connection_url().replace("psycopg2", "asyncpg")
        cfg = _alembic_config(async_url)

        await asyncio.to_thread(command.upgrade, cfg, "head")

        engine = build_engine(async_url)
        factory = build_session_factory(engine)
        async with factory() as session:
            session.add(AuditEvent(action="test", target_type="t", target_id="1"))
            await session.commit()
            row = (
                await session.execute(
                    sa.select(AuditEvent).where(AuditEvent.action == "test")
                )
            ).scalar_one()
            # Capture the id now: session.rollback() below expires all ORM
            # instances, and re-touching `row.id` afterward would trigger an
            # implicit sync refresh that raises MissingGreenlet under async
            # SQLAlchemy -- unrelated to the trigger behavior under test.
            row_id = row.id

            with pytest.raises((DBAPIError, IntegrityError)):
                await session.execute(
                    sa.text("UPDATE audit_events SET action = 'tampered' WHERE id = :id"),
                    {"id": row_id},
                )
                await session.commit()
            await session.rollback()

            with pytest.raises((DBAPIError, IntegrityError)):
                await session.execute(
                    sa.text("DELETE FROM audit_events WHERE id = :id"), {"id": row_id}
                )
                await session.commit()
            await session.rollback()
        await engine.dispose()
