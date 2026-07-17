from collections.abc import AsyncIterator, Iterator

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from testcontainers.postgres import PostgresContainer

from raghub.api.app import create_app
from raghub.core.db import Base, build_engine, build_session_factory
from raghub.modules.auth.models import User
from raghub.modules.auth.passwords import hash_password
from raghub.modules.tenancy.models import Organization


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


@pytest.fixture
async def client(engine: AsyncEngine) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(session_factory=build_session_factory(engine))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
async def seeded_user(session: AsyncSession) -> User:
    org = Organization(name="Acme")
    session.add(org)
    await session.flush()
    user = User(
        org_id=org.id, email="a@acme.com", password_hash=hash_password("pw123456"), role="admin"
    )
    session.add(user)
    await session.commit()
    return user
