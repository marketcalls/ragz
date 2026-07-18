from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from raghub.core.config import Settings
from raghub.modules.audit.models import AuditEvent
from raghub.modules.auth.models import User
from raghub.modules.auth.passwords import hash_password
from raghub.modules.auth.service import login
from raghub.modules.tenancy.models import Organization


async def test_login_writes_audit(session: AsyncSession) -> None:
    org = Organization(name="A")
    session.add(org)
    await session.flush()
    session.add(User(org_id=org.id, email="a@a.com",
                     password_hash=hash_password("pw123456"), role="user"))
    await session.commit()

    await login(
        session, email="a@a.com", password="pw123456", settings=Settings(_env_file=None)  # noqa: S106
    )
    events = list((await session.execute(select(AuditEvent))).scalars())
    assert [e.action for e in events] == ["login.success"]
    assert events[0].org_id == org.id
