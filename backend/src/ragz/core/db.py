import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from fastapi import Request
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.sql import Select


class Base(DeclarativeBase):
    pass


def naive_utc() -> datetime:
    """Naive-UTC now, the single write-path idiom (see ADR-0003)."""
    return datetime.now(UTC).replace(tzinfo=None)


class UUIDPk:
    """Mixin: uuid4 primary key + created_at."""

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(default=naive_utc)


def build_engine(
    url: str, *, pool_size: int = 10, max_overflow: int = 20, pool_timeout: int = 30
) -> AsyncEngine:
    return create_async_engine(
        url, pool_pre_ping=True, pool_size=pool_size,
        max_overflow=max_overflow, pool_timeout=pool_timeout,
    )


def build_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def committed_row_exists_after_error(
    session: AsyncSession, statement: Select[tuple[Any]]
) -> bool | None:
    """Resolve an ambiguous commit outcome without allowing cancellation to abort it.

    ``False`` is the only result that permits destructive external compensation.
    ``None`` means the database outcome could not be established and callers must
    preserve the object for reconciliation rather than risk deleting committed data.
    """

    async def inspect() -> bool | None:
        try:
            await session.rollback()
            return (await session.scalar(statement)) is not None
        except BaseException:
            return None

    task = asyncio.create_task(inspect())
    while True:
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            if task.done():
                return task.result()


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    factory = request.app.state.session_factory
    session = factory()
    unwinding = False
    try:
        yield session
    except BaseException:
        unwinding = True
        raise
    finally:
        # Starlette finalizes yield dependencies inside the request's cancel
        # scope.  An SSE disconnect can therefore cancel AsyncSession.close()
        # while it is rolling back an idle transaction, leaving the asyncpg
        # connection checked out until garbage collection.  Keep the close in
        # an independent task and wait through repeated cancel-scope delivery;
        # then re-raise cancellation so request teardown semantics are intact.
        close_task = asyncio.create_task(session.close())
        interruption: asyncio.CancelledError | None = None
        while not close_task.done():
            try:
                await asyncio.shield(close_task)
            except asyncio.CancelledError as exc:
                if interruption is None:
                    interruption = exc
        close_task.result()
        if interruption is not None and not unwinding:
            raise interruption


# --- per-loop engine reuse (ADR-0006) ---------------------------------------
# asyncpg connections are bound to the event loop that created them, so ONE
# module-level engine is not safe here: ingest._session is used by the Celery
# worker AND by the API (routes nudge the outbox dispatcher, which opens a
# session). Those are different loops in the same codebase, and sharing an
# engine between them fails with "Event loop is closed".
#
# Keying the cache by running loop gives process-lifetime reuse WITHIN a loop
# while staying correct across them. Paired with ADR-0006's one-loop-per-worker
# process, that is exactly one engine per worker -- which is the point: before
# this, every task built and disposed its own pool, including the outbox sweep
# that runs every 30 seconds.
_ENGINES: dict[asyncio.AbstractEventLoop, AsyncEngine] = {}


def get_loop_engine(url: str) -> AsyncEngine:
    """The engine for the CURRENT event loop, created on first use.

    Entries for closed loops are dropped on the way through. Without that the
    map grows one engine per loop ever used and holds a strong reference to
    each loop, so neither the loop nor its pool can ever be collected -- a
    process that creates loops repeatedly (the test suite does, one per test)
    accumulates dead pools indefinitely. A closed loop's connections are
    already unusable, so there is nothing to dispose, only to release.
    """
    for dead in [loop for loop in _ENGINES if loop.is_closed()]:
        del _ENGINES[dead]
    loop = asyncio.get_running_loop()
    engine = _ENGINES.get(loop)
    if engine is None:
        engine = build_engine(url)
        _ENGINES[loop] = engine
    return engine


async def dispose_loop_engine() -> None:
    """Dispose the current loop's engine, if any. For a worker process shutting
    down, and for tests that need a clean slate -- without it a torn-down loop
    would leave its pool behind in _ENGINES forever."""
    loop = asyncio.get_running_loop()
    engine = _ENGINES.pop(loop, None)
    if engine is not None:
        await engine.dispose()
