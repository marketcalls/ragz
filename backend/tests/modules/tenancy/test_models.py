from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ragz.modules.auth.models import User
from ragz.modules.tenancy.models import Organization


async def test_create_org_and_user(session: AsyncSession) -> None:
    org = Organization(name="Acme")
    session.add(org)
    await session.flush()
    user = User(org_id=org.id, email="a@acme.com", password_hash="x", role="admin")  # noqa: S106
    session.add(user)
    await session.commit()

    found = (await session.execute(select(User).where(User.email == "a@acme.com"))).scalar_one()
    assert found.org_id == org.id
    assert found.active is True
