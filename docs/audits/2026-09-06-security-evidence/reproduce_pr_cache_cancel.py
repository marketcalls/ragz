import asyncio
import sys
import types


class _Metric:
    def __init__(self, *args, **kwargs):
        pass

    def labels(self, *args, **kwargs):
        return self

    def inc(self, *args, **kwargs):
        pass

    def observe(self, *args, **kwargs):
        pass


# The shared test venv is intentionally minimal. Stub metrics only; the cache
# behavior under test is otherwise the exact PR implementation.
prometheus_client = types.ModuleType("prometheus_client")
prometheus_client.CONTENT_TYPE_LATEST = "text/plain"
prometheus_client.Counter = _Metric
prometheus_client.Histogram = _Metric
prometheus_client.generate_latest = lambda: b""
sys.modules["prometheus_client"] = prometheus_client

from ragz.modules.retrieval.embeddings import InMemoryQueryEmbeddingCache
from ragz.modules.retrieval.query_expansion import (
    ExpandedQueries,
    InMemoryQueryExpansionCache,
)


async def reproduce_embedding_dead_slot() -> bool:
    cache = InMemoryQueryEmbeddingCache()
    compute_started = asyncio.Event()
    release_compute = asyncio.Event()

    async def compute(texts):
        compute_started.set()
        await release_compute.wait()
        return [[1.0] for _ in texts], 1

    owner = asyncio.create_task(cache.get_or_compute("namespace", ["query"], compute))
    await compute_started.wait()
    await cache._lock.acquire()
    release_compute.set()
    await asyncio.sleep(0)
    owner.cancel()
    try:
        await owner
    except asyncio.CancelledError:
        pass
    cache._lock.release()

    try:
        await asyncio.wait_for(
            cache.get_or_compute("namespace", ["query"], compute), timeout=0.05
        )
    except TimeoutError:
        return True
    return False


async def reproduce_expansion_dead_slot() -> bool:
    cache = InMemoryQueryExpansionCache()
    compute_started = asyncio.Event()
    release_compute = asyncio.Event()

    async def compute():
        compute_started.set()
        await release_compute.wait()
        return ExpandedQueries(("query", "alternative"), 1, 1)

    kwargs = {
        "model": "model",
        "max_queries": 3,
        "query": "query",
        "compute": compute,
    }
    owner = asyncio.create_task(cache.get_or_compute(**kwargs))
    await compute_started.wait()
    await cache._lock.acquire()
    release_compute.set()
    await asyncio.sleep(0)
    owner.cancel()
    try:
        await owner
    except asyncio.CancelledError:
        pass
    cache._lock.release()

    try:
        await asyncio.wait_for(cache.get_or_compute(**kwargs), timeout=0.05)
    except TimeoutError:
        return True
    return False


async def main() -> None:
    print(f"embedding_dead_slot={await reproduce_embedding_dead_slot()}")
    print(f"expansion_dead_slot={await reproduce_expansion_dead_slot()}")


asyncio.run(main())
