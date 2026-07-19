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
