"""ADR-0006: one event loop per worker process.

The point of these is the property asyncio.run could not provide -- an engine
that survives across task invocations. Before ADR-0006 every task got a fresh
loop, asyncpg connections are bound to their creating loop, and so every task
built and disposed its own pool: a TCP connect, TLS handshake and auth round
trip per parse, chunk, embed, delete, and per 30-second outbox sweep.
"""

import asyncio

import pytest
from sqlalchemy import text

from ragz.core.config import get_settings
from ragz.core.db import _ENGINES, build_session_factory, get_loop_engine
from ragz.worker import loop as worker_loop


def test_the_loop_is_reused_across_calls_and_survives_them() -> None:
    """asyncio.run closes its loop; this one must not, or nothing opened on it
    would still be usable by the next task."""
    first = worker_loop.get_loop()
    assert worker_loop.run(_identity(1)) == 1
    assert worker_loop.get_loop() is first, "a new loop per task is the old behaviour"
    assert not first.is_closed(), "closing the loop would invalidate its connections"


def test_a_closed_loop_is_replaced_rather_than_fatal() -> None:
    """A worker that somehow lost its loop should degrade to building another,
    not fail every task from that point on."""
    doomed = worker_loop.get_loop()
    doomed.close()

    replacement = worker_loop.get_loop()

    assert replacement is not doomed
    assert not replacement.is_closed()
    assert worker_loop.run(_identity(2)) == 2


def test_one_engine_serves_every_task_on_the_process_loop() -> None:
    """The whole point of ADR-0006: consecutive tasks share a pool.

    Reusing an engine across asyncio.run calls raised "Event loop is closed";
    that this passes at all is the behaviour change.
    """
    url = get_settings().database_url

    async def _task() -> tuple[int, int | None]:
        engine = get_loop_engine(url)
        async with build_session_factory(engine)() as session:
            return id(engine), await session.scalar(text("select 1"))

    first_id, first_value = worker_loop.run(_task())
    second_id, second_value = worker_loop.run(_task())

    assert first_value == second_value == 1
    assert first_id == second_id, "a per-task engine is exactly the churn this removes"


def test_shutdown_disposes_the_engine_and_closes_the_loop() -> None:
    """Keeping connections open across tasks means something has to close them,
    or a worker exits still holding Postgres connections."""
    url = get_settings().database_url
    loop = worker_loop.get_loop()
    worker_loop.run(_touch_engine(url))
    assert loop in _ENGINES, "precondition: an engine is cached for this loop"

    worker_loop.shutdown()

    # Scoped to THIS loop: _ENGINES is process-wide and the test session's own
    # per-test loops appear in it too, so asserting it is globally empty would
    # pass only when this file runs alone.
    assert loop not in _ENGINES, "shutdown must not leak this loop's pool"
    # Usable again afterwards, so shutdown is not a one-way door.
    assert worker_loop.run(_identity(3)) == 3


async def test_run_from_a_running_loop_says_what_to_do_instead() -> None:
    """There is no way to run a coroutine to completion on an already-running
    loop from inside it -- asyncio.run refuses just as run_until_complete does.
    So this raises deliberately, with a message that names the fix, rather than
    asyncio's bare "cannot be called from a running event loop"."""
    assert asyncio.get_running_loop() is not None

    with pytest.raises(RuntimeError, match="await the underlying coroutine"):
        worker_loop.run(_identity(4))


async def _identity(value: int) -> int:
    return value


async def _touch_engine(url: str) -> None:
    engine = get_loop_engine(url)
    async with build_session_factory(engine)() as session:
        await session.scalar(text("select 1"))


def test_engines_for_closed_loops_are_not_retained() -> None:
    """_ENGINES is keyed by loop and would otherwise grow one entry per loop
    ever used, holding a strong reference to each -- so neither the dead loop
    nor its connection pool could be collected. Found by the full suite, where
    per-test loops accumulated; a single-file run never showed it.
    """
    url = get_settings().database_url

    doomed = worker_loop.get_loop()
    worker_loop.run(_touch_engine(url))
    assert doomed in _ENGINES
    doomed.close()  # closed WITHOUT shutdown(), the case that leaked

    # Any later use must sweep the dead entry rather than accumulate it.
    worker_loop.run(_touch_engine(url))

    assert doomed not in _ENGINES
    assert not any(loop.is_closed() for loop in _ENGINES)
