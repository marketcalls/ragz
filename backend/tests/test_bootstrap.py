from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from raghub.bootstrap import bootstrap_superadmin
from raghub.core.db import build_session_factory
from raghub.modules.auth.models import User


async def test_bootstrap_idempotent(engine: AsyncEngine) -> None:
    factory = build_session_factory(engine)
    created = await bootstrap_superadmin(
        factory, email="root@x.com", password="rootpw12345"  # noqa: S106
    )
    assert created is True
    created_again = await bootstrap_superadmin(
        factory, email="root@x.com", password="rootpw12345"  # noqa: S106
    )
    assert created_again is False
    async with factory() as s:
        user = (await s.execute(select(User).where(User.email == "root@x.com"))).scalar_one()
        assert user.role == "superadmin"
