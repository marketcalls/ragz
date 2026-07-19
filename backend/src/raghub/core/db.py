from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi import Request
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


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


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    factory = request.app.state.session_factory
    async with factory() as session:
        yield session
