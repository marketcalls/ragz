from functools import lru_cache

from qdrant_client import AsyncQdrantClient

from ragz.core.config import get_settings

COLLECTION = "chunks_bge_m3"  # one collection per embedding model (foundation)
EPHEMERAL_COLLECTION = "ephemeral_attachments"  # per-chat attachments, own store


@lru_cache
def get_qdrant() -> AsyncQdrantClient:
    return AsyncQdrantClient(url=get_settings().qdrant_url)
