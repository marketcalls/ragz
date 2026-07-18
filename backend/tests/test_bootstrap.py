from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from raghub.bootstrap import bootstrap_superadmin
from raghub.core.db import build_session_factory
from raghub.modules.auth.models import User
from raghub.modules.tenancy.models import Organization


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


async def test_bootstrap_race_with_existing_platform_org(
    engine: AsyncEngine,
) -> None:
    """Simulate TOCTOU race: Platform org exists but no superadmin — bootstrap returns False."""
    factory = build_session_factory(engine)
    # Pre-create Platform organization (simulating another replica winning the race)
    async with factory() as session:
        org = Organization(name="Platform")
        session.add(org)
        await session.commit()

    # bootstrap_superadmin should idempotently return False without raising IntegrityError
    result = await bootstrap_superadmin(
        factory, email="root@example.com", password="testpw123456"  # noqa: S106
    )
    assert result is False
