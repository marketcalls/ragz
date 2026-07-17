from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def test_roundtrip(session: AsyncSession) -> None:
    result = await session.execute(text("SELECT 1"))
    assert result.scalar() == 1
