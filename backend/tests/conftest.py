from collections.abc import AsyncIterator, Iterator

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from testcontainers.postgres import PostgresContainer

from raghub.core.db import Base, build_engine, build_session_factory


@pytest.fixture(scope="session")
def pg_url() -> Iterator[str]:
    with PostgresContainer("postgres:16-alpine") as pg:
        yield pg.get_connection_url().replace("psycopg2", "asyncpg")


@pytest.fixture
async def engine(pg_url: str) -> AsyncIterator[AsyncEngine]:
    eng = build_engine(pg_url)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await eng.dispose()


@pytest.fixture
async def session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    factory = build_session_factory(engine)
    async with factory() as s:
        yield s
