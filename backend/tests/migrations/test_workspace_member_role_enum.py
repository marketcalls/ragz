"""RBAC-08: the forward migration that backfills every non-conforming
`workspace_members.role` value to 'contributor' (capability-preserving --
role has never gated any authorization decision), promotes exactly one
'owner' per workspace that has members but no owner yet, then locks the
column down with a CHECK constraint.

Mirrors tests/migrations/test_seed_contributor_role.py /
test_content_manager_admin_grant.py's pattern: a dedicated, fresh
PostgresContainer driven through the real Alembic chain -- upgraded first to
the revision immediately before this one, seeded with a pre-migration
`workspace_members` row carrying a legacy free-string role and no owner for
that workspace, then upgraded to head. `command.upgrade` runs via
`asyncio.to_thread` so its internal `asyncio.run()` doesn't collide with the
already-running pytest-asyncio event loop.
"""

import asyncio
import uuid

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from testcontainers.postgres import PostgresContainer

from ragz.core.db import build_engine, build_session_factory
from ragz.modules.auth.passwords import hash_password
from ragz.modules.models.models import LOCAL_EMBEDDING_MODEL_ID

# Schema state immediately BEFORE this migration (Task 9's content-manager
# admin grant -- Task 10 made no schema change, so it's still the head).
_PRE_MIGRATION_REVISION = "f0a1b2c3d4e5"


def _alembic_config(async_url: str) -> Config:
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", async_url)
    return cfg


async def test_role_backfilled_owner_promoted_and_check_constraint_enforced() -> None:
    with PostgresContainer("postgres:16-alpine") as pg:
        async_url = pg.get_connection_url().replace("psycopg2", "asyncpg")
        cfg = _alembic_config(async_url)

        await asyncio.to_thread(command.upgrade, cfg, _PRE_MIGRATION_REVISION)

        engine = build_engine(async_url)
        factory = build_session_factory(engine)
        org_id = uuid.uuid4()
        ws_id = uuid.uuid4()
        user_id = uuid.uuid4()
        async with factory() as session:
            await session.execute(
                sa.text(
                    "INSERT INTO organizations (id, created_at, name) "
                    "VALUES (:id, now(), :name)"
                ),
                {"id": org_id, "name": "PreMigrationOrg"},
            )
            await session.execute(
                sa.text(
                    "INSERT INTO users (id, created_at, org_id, email, password_hash, role, "
                    "active, custom_role_id) VALUES (:id, now(), :org_id, :email, :ph, 'user', "
                    "true, NULL)"
                ),
                {"id": user_id, "org_id": org_id, "email": "member@x.com",
                 "ph": hash_password("pw123456x")},
            )
            await session.execute(
                sa.text(
                    "INSERT INTO workspaces (id, created_at, org_id, name, embedding_model_id, "
                    "min_score, default_model_id, top_k, rerank_enabled) "
                    "VALUES (:id, now(), :org_id, :name, :emb_id, :min_score, NULL, :top_k, "
                    "false)"
                ),
                {"id": ws_id, "org_id": org_id, "name": "PreMigrationWS",
                 "emb_id": LOCAL_EMBEDDING_MODEL_ID, "min_score": 0.35, "top_k": 8},
            )
            # No 'owner' row exists for this workspace, and the legacy free
            # string 'member' pre-dates the enum entirely.
            await session.execute(
                sa.text(
                    "INSERT INTO workspace_members (workspace_id, user_id, role) "
                    "VALUES (:ws_id, :user_id, 'member')"
                ),
                {"ws_id": ws_id, "user_id": user_id},
            )
            await session.commit()
        await engine.dispose()

        await asyncio.to_thread(command.upgrade, cfg, "head")

        engine = build_engine(async_url)
        factory = build_session_factory(engine)
        async with factory() as session:
            rows = (
                await session.execute(
                    sa.text(
                        "SELECT user_id, role FROM workspace_members WHERE workspace_id = :ws_id"
                    ),
                    {"ws_id": ws_id},
                )
            ).all()
            roles_by_user = {r[0]: r[1] for r in rows}
            # The sole member (also the lowest/only user_id for this
            # workspace) is promoted to 'owner' since none existed.
            assert roles_by_user == {user_id: "owner"}

            # A second, distinct user -- so the rejected insert below fails on
            # the CHECK constraint specifically, not an unrelated PK/FK clash.
            other_user_id = uuid.uuid4()
            await session.execute(
                sa.text(
                    "INSERT INTO users (id, created_at, org_id, email, password_hash, role, "
                    "active, custom_role_id) VALUES (:id, now(), :org_id, :email, :ph, 'user', "
                    "true, NULL)"
                ),
                {"id": other_user_id, "org_id": org_id, "email": "other@x.com",
                 "ph": hash_password("pw123456x")},
            )
            await session.commit()

            # The CHECK constraint now rejects a bogus role value outright.
            # session.execute auto-begins a transaction on first use here, so
            # the failure below must be followed by a rollback before the
            # session (or the `with` block's implicit close) is touched again.
            with pytest.raises(sa.exc.DBAPIError):
                await session.execute(
                    sa.text(
                        "INSERT INTO workspace_members (workspace_id, user_id, role) "
                        "VALUES (:ws_id, :user_id, 'bogus')"
                    ),
                    {"ws_id": ws_id, "user_id": other_user_id},
                )
            await session.rollback()
        await engine.dispose()
