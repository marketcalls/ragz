from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ragz.core.config import Settings
from ragz.modules.audit.models import AuditEvent
from ragz.modules.auth.models import User
from ragz.modules.auth.passwords import hash_password
from ragz.modules.auth.service import login
from ragz.modules.tenancy.models import Organization


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
