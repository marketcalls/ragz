"""Forward compatibility for durable security-remediation state."""

import asyncio

import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from testcontainers.postgres import PostgresContainer

from ragz.core.db import build_engine, build_session_factory
from tests.migrations._historical import insert_org

_PRE_REMEDIATION_REVISION = "6a8d2c4f1b90"
_LEGACY_ATTACHMENT_BYTES = 50 * 1024 * 1024


async def test_legacy_attachment_size_is_conservatively_backfilled() -> None:
    with PostgresContainer("postgres:16-alpine") as postgres:
        async_url = postgres.get_connection_url().replace("psycopg2", "asyncpg")
        config = Config("alembic.ini")
        config.set_main_option("sqlalchemy.url", async_url)
        await asyncio.to_thread(command.upgrade, config, _PRE_REMEDIATION_REVISION)

        engine = build_engine(async_url)
        factory = build_session_factory(engine)
        async with factory() as session:
            org_id = await insert_org(session, "LegacyAttachmentOrg")
            user_id = (
                await session.execute(
                    sa.text(
                        "INSERT INTO users "
                        "(id, created_at, org_id, email, password_hash, role, active) "
                        "VALUES (gen_random_uuid(), now(), :org_id, "
                        "'legacy@example.test', 'x', 'user', true) RETURNING id"
                    ),
                    {"org_id": org_id},
                )
            ).scalar_one()
            workspace_id = (
                await session.execute(
                    sa.text(
                        "INSERT INTO workspaces (id, created_at, org_id, name, min_score) "
                        "VALUES (gen_random_uuid(), now(), :org_id, 'legacy', 0.35) "
                        "RETURNING id"
                    ),
                    {"org_id": org_id},
                )
            ).scalar_one()
            chat_id = (
                await session.execute(
                    sa.text(
                        "INSERT INTO chats "
                        "(id, created_at, org_id, workspace_id, user_id, title, updated_at) "
                        "VALUES (gen_random_uuid(), now(), :org_id, :workspace_id, "
                        ":user_id, 'legacy', now()) RETURNING id"
                    ),
                    {
                        "org_id": org_id,
                        "workspace_id": workspace_id,
                        "user_id": user_id,
                    },
                )
            ).scalar_one()
            attachment_id = (
                await session.execute(
                    sa.text(
                        "INSERT INTO chat_attachments "
                        "(id, created_at, chat_id, kind, filename, mime, storage_key, "
                        "status, extracted_text, routed_to, message_id) "
                        "VALUES (gen_random_uuid(), now(), :chat_id, 'document', "
                        "'legacy.pdf', 'application/pdf', 'legacy/key', 'ready', "
                        "NULL, NULL, NULL) RETURNING id"
                    ),
                    {"chat_id": chat_id},
                )
            ).scalar_one()
            await session.commit()
        await engine.dispose()

        await asyncio.to_thread(command.upgrade, config, "head")

        engine = build_engine(async_url)
        factory = build_session_factory(engine)
        async with factory() as session:
            size_bytes = await session.scalar(
                sa.text(
                    "SELECT size_bytes FROM chat_attachments WHERE id = :attachment_id"
                ),
                {"attachment_id": attachment_id},
            )
            assert size_bytes == _LEGACY_ATTACHMENT_BYTES
            cleanup_columns = {
                row["column_name"]
                for row in (
                    await session.execute(
                        sa.text(
                            "SELECT column_name FROM information_schema.columns "
                            "WHERE table_name = 'attachment_cleanup_jobs'"
                        )
                    )
                ).mappings()
            }
            assert {"org_id", "user_id", "attachment_id", "size_bytes"} <= cleanup_columns
        await engine.dispose()
