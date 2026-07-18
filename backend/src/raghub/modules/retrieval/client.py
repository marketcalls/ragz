from functools import lru_cache

from qdrant_client import AsyncQdrantClient

from raghub.core.config import get_settings

COLLECTION = "chunks_bge_m3"  # one collection per embedding model (foundation)


@lru_cache
def get_qdrant() -> AsyncQdrantClient:
    return AsyncQdrantClient(url=get_settings().qdrant_url)
