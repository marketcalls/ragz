"""Smoke test: run the real alembic migration chain end-to-end.

Unlike the rest of the test suite (which builds schema via
``Base.metadata.create_all`` for speed), this test exercises the actual
alembic chain against a fresh Postgres instance so that a broken
migration (bad SQL, wrong down_revision, etc.) is caught before merge.
"""

import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from testcontainers.postgres import PostgresContainer

from raghub.core.config import get_settings

EXPECTED_TABLES = {
    "organizations",
    "users",
    "workspaces",
    "workspace_members",
    "invitations",
    "refresh_tokens",
    "app_settings",
    "audit_events",
    "documents",
    "ingest_jobs",
    "secrets",
    "models",
    "chats",
    "messages",
    "citations",
    "groups",
    "user_groups",
    "org_quotas",
    "user_quotas",
    "usage_records",
    "model_catalog",
    "metadata_fields",
    "role_templates",
    "golden_queries",
    "eval_runs",
    "alembic_version",
}


def test_migration_chain_upgrades_to_head() -> None:
    with PostgresContainer("postgres:16-alpine") as pg:
        async_url = pg.get_connection_url().replace("psycopg2", "asyncpg")

        cfg = Config("alembic.ini")
        cfg.set_main_option("sqlalchemy.url", async_url)
        command.upgrade(cfg, "head")

        sync_engine = sa.create_engine(pg.get_connection_url())
        try:
            with sync_engine.connect() as conn:
                inspector = sa.inspect(conn)
                tables = set(inspector.get_table_names())
                assert EXPECTED_TABLES <= tables

                # The REVOKE in the audit_events migration isn't assertable
                # under the testcontainers superuser role (it never applies
                # to a superuser); just confirm the migration ran cleanly and
                # the table is queryable.
                result = conn.execute(sa.text("select count(*) from audit_events"))
                assert result.scalar() == 0
        finally:
            sync_engine.dispose()


def test_migration_seeds_local_embedding_model() -> None:
    """DOC-10 (d1e8f4a2b6c3): the migration's INSERT seeds the bootstrap local
    TEI embedding model with the EXACT pre-existing "chunks_bge_m3" collection
    name and a dimension read from settings at migration time -- verified here
    (not against the rest of the suite's `session` fixture, which builds
    schema via Base.metadata.create_all and never runs this INSERT; see this
    file's module docstring)."""
    with PostgresContainer("postgres:16-alpine") as pg:
        async_url = pg.get_connection_url().replace("psycopg2", "asyncpg")

        cfg = Config("alembic.ini")
        cfg.set_main_option("sqlalchemy.url", async_url)
        command.upgrade(cfg, "head")

        sync_engine = sa.create_engine(pg.get_connection_url())
        try:
            with sync_engine.connect() as conn:
                row = conn.execute(
                    sa.text(
                        "SELECT litellm_model_name, provider_kind, modality, "
                        "dimension, collection_name, enabled, sync_status "
                        "FROM models WHERE id = :id"
                    ),
                    {"id": "00000000-0000-4000-8000-000000000001"},
                ).one()
                assert row.litellm_model_name == "local-embeddings"
                assert row.provider_kind == "tei"
                assert row.modality == "embedding"
                assert row.collection_name == "chunks_bge_m3"
                assert row.dimension == get_settings().embedding_dim
                assert row.enabled is True
                assert row.sync_status == "synced"
        finally:
            sync_engine.dispose()
