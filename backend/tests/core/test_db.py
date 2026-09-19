import asyncio
from types import SimpleNamespace

import anyio
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from ragz.core.db import build_engine, build_session_factory, get_session


async def test_roundtrip(session: AsyncSession) -> None:
    result = await session.execute(text("SELECT 1"))
    assert result.scalar() == 1


def test_engine_pool_sizing_configurable() -> None:
    eng = build_engine("postgresql+asyncpg://x:y@localhost/db", pool_size=3, max_overflow=7)
    assert eng.pool.size() == 3  # sync_engine pool reflects the setting


async def test_request_session_cleanup_survives_cancel_scope(engine: AsyncEngine) -> None:
    """An SSE disconnect must return an idle transaction to the pool.

    Starlette finalizes yield dependencies inside the request's cancelled
    scope.  A plain ``async with factory()`` lets that cancellation interrupt
    ``AsyncSession.close()``, leaving the checked-out asyncpg connection for
    garbage collection instead of the pool.
    """

    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(session_factory=build_session_factory(engine)))
    )
    dependency = get_session(request)  # type: ignore[arg-type]
    session = await anext(dependency)
    await session.execute(text("SELECT 1"))
    assert engine.pool.checkedout() == 1  # type: ignore[attr-defined]

    with anyio.CancelScope() as scope:
        scope.cancel()
        await dependency.aclose()

    assert engine.pool.checkedout() == 0  # type: ignore[attr-defined]
    async with build_session_factory(engine)() as verifier:
        assert await verifier.scalar(text("SELECT 1")) == 1


async def test_request_session_cleanup_preserves_cancellation_that_starts_during_close() -> None:
    close_started = asyncio.Event()
    release_close = asyncio.Event()

    class BlockingSession:
        closed = False

        async def close(self) -> None:
            close_started.set()
            await release_close.wait()
            self.closed = True

    session = BlockingSession()
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(session_factory=lambda: session))
    )
    dependency = get_session(request)  # type: ignore[arg-type]
    assert await anext(dependency) is session

    cleanup = asyncio.create_task(anext(dependency))
    await close_started.wait()
    cleanup.cancel("request-disconnected")
    release_close.set()

    with pytest.raises(asyncio.CancelledError) as cancelled:
        await cleanup

    assert cancelled.value.args == ("request-disconnected",)
    assert session.closed
