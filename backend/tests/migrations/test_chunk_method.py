"""Chunk-methods plan Task 2: the forward migration that adds
`workspaces.chunk_method` (NOT NULL, server_default 'heading') and
`documents.chunk_method_override` (nullable), each CHECK-constrained to the
closed strategy set the pipeline's `chunk_document` dispatcher understands.

Mirrors tests/migrations/test_seed_contributor_role.py's pattern: a
dedicated, fresh PostgresContainer driven through the real Alembic chain
(not the create_all()-based `engine` fixture from tests/conftest.py, which
bypasses Alembic entirely and would collide with "relation already
exists"), upgraded first to the revision just before this one to get a
pre-migration schema, seeded with a pre-existing workspace, then upgraded to
head. Each `command.upgrade` call is dispatched via `asyncio.to_thread`
because alembic/env.py drives migrations through its own internal
`asyncio.run(...)`, which cannot be called from this test's already-running
event loop (pytest-asyncio, `asyncio_mode = "auto"`).
"""

import asyncio

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.exc import IntegrityError
from testcontainers.postgres import PostgresContainer

from ragz.core.db import build_engine, build_session_factory
from ragz.modules.auth.passwords import hash_password
from ragz.modules.tenancy.models import Organization

_PRE_MIGRATION_REVISION = "43a13912a44c"


def _alembic_config(async_url: str) -> Config:
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", async_url)
    return cfg


async def test_chunk_method_backfilled_and_check_constrained() -> None:
    with PostgresContainer("postgres:16-alpine") as pg:
        async_url = pg.get_connection_url().replace("psycopg2", "asyncpg")
        cfg = _alembic_config(async_url)

        # Schema as it existed immediately before this migration.
        await asyncio.to_thread(command.upgrade, cfg, _PRE_MIGRATION_REVISION)

        engine = build_engine(async_url)
        factory = build_session_factory(engine)
        async with factory() as session:
            org = Organization(name="PreMigrationOrg")
            session.add(org)
            await session.flush()
            ws_result = await session.execute(
                sa.text(
                    "INSERT INTO workspaces (id, created_at, org_id, name, embedding_model_id, "
                    "min_score, top_k, rerank_enabled, fallback_policy, web_search_enabled, "
                    "strict_mode, enrichment_enabled) "
                    "VALUES (gen_random_uuid(), now(), :org_id, 'pre-ws', "
                    "(SELECT id FROM models LIMIT 1), 0.35, 8, false, 'general_knowledge', "
                    "false, false, false) RETURNING id"
                ),
                {"org_id": org.id},
            )
            pre_ws_id = ws_result.scalar_one()

            user_result = await session.execute(
                sa.text(
                    "INSERT INTO users (id, created_at, org_id, email, password_hash, role, "
                    "active, custom_role_id) VALUES (gen_random_uuid(), now(), :org_id, "
                    ":email, :ph, 'user', true, NULL) RETURNING id"
                ),
                {"org_id": org.id, "email": "pre@chunk.com", "ph": hash_password("pw123456x")},
            )
            pre_user_id = user_result.scalar_one()
            await session.commit()
        await engine.dispose()

        await asyncio.to_thread(command.upgrade, cfg, "head")

        engine = build_engine(async_url)
        factory = build_session_factory(engine)
        async with factory() as session:
            row = (
                await session.execute(
                    sa.text("SELECT chunk_method FROM workspaces WHERE id = :id"),
                    {"id": pre_ws_id},
                )
            ).first()
            assert row is not None and row[0] == "heading"

            with pytest.raises(IntegrityError):
                await session.execute(
                    sa.text(
                        "UPDATE workspaces SET chunk_method = 'bogus' WHERE id = :id"
                    ),
                    {"id": pre_ws_id},
                )
                await session.flush()
            await session.rollback()

            doc_result = await session.execute(
                sa.text(
                    "INSERT INTO documents (id, created_at, org_id, workspace_id, filename, "
                    "mime, size_bytes, content_hash, status, storage_key, created_by, "
                    "updated_at, pinned, version, lineage_id, is_current, approved, "
                    "vectors_present, enriched) "
                    "VALUES (gen_random_uuid(), now(), :org_id, :ws_id, 'f.txt', 'text/plain', "
                    "1, 'h1', 'queued', 'k1', "
                    ":created_by, now(), false, 1, gen_random_uuid(), "
                    "false, false, false, false) RETURNING id"
                ),
                {"org_id": org.id, "ws_id": pre_ws_id, "created_by": pre_user_id},
            )
            doc_id = doc_result.scalar_one()
            await session.commit()

            doc_row = (
                await session.execute(
                    sa.text(
                        "SELECT chunk_method_override FROM documents WHERE id = :id"
                    ),
                    {"id": doc_id},
                )
            ).first()
            assert doc_row is not None and doc_row[0] is None

            with pytest.raises(IntegrityError):
                await session.execute(
                    sa.text(
                        "UPDATE documents SET chunk_method_override = 'bogus' "
                        "WHERE id = :id"
                    ),
                    {"id": doc_id},
                )
                await session.flush()
            await session.rollback()
        await engine.dispose()
