"""Print a read-only inventory of legacy chat-attachment orphan candidates.

This command never deletes or schedules anything. Review its JSON output before
using a separate, explicitly authorized recovery procedure.
"""

import asyncio
import json
from uuid import UUID

from sqlalchemy import select

from ragz.core.config import get_settings
from ragz.core.db import build_engine, build_session_factory
from ragz.core.storage import build_storage
from ragz.modules.chat.cleanup import legacy_orphan_candidates
from ragz.modules.chat.models import AttachmentCleanupJob, ChatAttachment
from ragz.modules.retrieval.client import EPHEMERAL_COLLECTION, get_qdrant


def _attachment_id_from_key(key: str) -> UUID | None:
    parts = key.split("/")
    try:
        chats_index = parts.index("chats")
        return UUID(parts[chats_index + 2])
    except (ValueError, IndexError):
        return None


async def _object_attachment_ids() -> set[UUID]:
    storage = build_storage(get_settings())
    result: set[UUID] = set()
    async for key in storage.iter_keys():
        attachment_id = _attachment_id_from_key(key)
        if attachment_id is not None:
            result.add(attachment_id)
    return result


async def _vector_attachment_ids() -> set[UUID]:
    client = get_qdrant()
    if not await client.collection_exists(EPHEMERAL_COLLECTION):
        return set()
    result: set[UUID] = set()
    offset = None
    while True:
        points, offset = await client.scroll(
            EPHEMERAL_COLLECTION,
            limit=256,
            offset=offset,
            with_payload=["attachment_id"],
            with_vectors=False,
        )
        for point in points:
            value = (point.payload or {}).get("attachment_id")
            try:
                result.add(UUID(str(value)))
            except ValueError:
                continue
        if offset is None:
            return result


async def main() -> None:
    settings = get_settings()
    engine = build_engine(settings.database_url)
    try:
        factory = build_session_factory(engine)
        async with factory() as session:
            database_ids = set((await session.execute(select(ChatAttachment.id))).scalars())
            cleanup_ids = set(
                (await session.execute(select(AttachmentCleanupJob.attachment_id))).scalars()
            )
        candidates = legacy_orphan_candidates(
            database_attachment_ids=database_ids,
            cleanup_job_attachment_ids=cleanup_ids,
            object_attachment_ids=await _object_attachment_ids(),
            vector_attachment_ids=await _vector_attachment_ids(),
        )
        print(
            json.dumps(
                {
                    "mode": "read-only",
                    "object_attachment_ids": sorted(
                        str(value) for value in candidates.object_attachment_ids
                    ),
                    "vector_attachment_ids": sorted(
                        str(value) for value in candidates.vector_attachment_ids
                    ),
                },
                indent=2,
                sort_keys=True,
            )
        )
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
