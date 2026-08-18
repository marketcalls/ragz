"""One event loop per worker process (ADR-0006).

Every task used to call asyncio.run, which creates and destroys a loop per
task. asyncpg connections are bound to their creating loop, so the engine had
to be rebuilt and disposed per task too -- including for outbox.dispatch_pending,
which runs every 30 seconds. That is a TCP connect, TLS handshake and auth round
trip for every parse, chunk, embed, delete and sweep.

A loop that lives as long as the process lets core.db.get_loop_engine hand back
the same engine for the whole process, which is what ADR-0006 is for.

The loop is created lazily rather than only in worker_process_init, because
tasks are also invoked directly in tests and from the beat process, where that
signal never fires.
"""

import asyncio
from collections.abc import Coroutine
from typing import Any

import structlog

log = structlog.get_logger()

_LOOP: asyncio.AbstractEventLoop | None = None


def get_loop() -> asyncio.AbstractEventLoop:
    """This process's task loop, created on first use.

    Recreated if it was closed: a worker that lost its loop should degrade to
    building a new one, not fail every task from then on.
    """
    global _LOOP
    if _LOOP is None or _LOOP.is_closed():
        _LOOP = asyncio.new_event_loop()
        asyncio.set_event_loop(_LOOP)
    return _LOOP


def run[T](coro: Coroutine[Any, Any, T]) -> T:
    """Run a coroutine on this process's loop.

    Drop-in for asyncio.run inside tasks, with one deliberate difference: it
    does NOT close the loop afterwards, so connections opened on it stay valid
    for the next task.

    Must be called from sync context, which every Celery task body is. Calling
    it from inside a running loop raises with an explicit message rather than
    asyncio's "cannot be called from a running event loop", which says nothing
    about what to do instead. There is no fallback: asyncio.run refuses from a
    running loop exactly as run_until_complete does, so a fallback could only
    ever re-raise -- the pre-ADR-0006 code had the same constraint.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return get_loop().run_until_complete(coro)
    coro.close()
    raise RuntimeError(
        "worker.loop.run() was called from a running event loop. Task bodies "
        "are sync; from async code await the underlying coroutine directly "
        "instead of going through the task wrapper."
    )


def shutdown() -> None:
    """Dispose the loop's engine and close it. Called on worker shutdown so a
    process does not exit holding open Postgres connections."""
    global _LOOP
    if _LOOP is None or _LOOP.is_closed():
        return
    from ragz.core.db import dispose_loop_engine

    try:
        _LOOP.run_until_complete(dispose_loop_engine())
    except Exception as exc:  # noqa: BLE001 - shutdown must not raise
        log.warning("worker_loop_shutdown_dispose_failed", error=str(exc))
    finally:
        _LOOP.close()
        _LOOP = None
