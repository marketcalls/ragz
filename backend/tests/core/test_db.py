from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ragz.core.db import build_engine


async def test_roundtrip(session: AsyncSession) -> None:
    result = await session.execute(text("SELECT 1"))
    assert result.scalar() == 1


def test_engine_pool_sizing_configurable() -> None:
    eng = build_engine("postgresql+asyncpg://x:y@localhost/db", pool_size=3, max_overflow=7)
    assert eng.pool.size() == 3  # sync_engine pool reflects the setting
