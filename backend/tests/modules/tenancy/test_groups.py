import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from ragz.core.errors import NotFoundError
from ragz.modules.auth.models import User
from ragz.modules.tenancy.context import TenantContext
from ragz.modules.tenancy.models import Organization
from ragz.modules.tenancy.service import (
    add_group_member,
    create_group,
    delete_group,
    list_groups,
    remove_group_member,
)


async def seed(session: AsyncSession) -> tuple[TenantContext, User, Organization]:
    org = Organization(name="GrpOrg")
    session.add(org)
    await session.flush()
    user = User(org_id=org.id, email="g@grporg.com", password_hash="x", role="user")  # noqa: S106
    session.add(user)
    await session.commit()
    ctx = TenantContext(user_id=user.id, org_id=org.id, role="admin", workspace_ids=frozenset())
    return ctx, user, org


async def test_group_crud_and_membership(session: AsyncSession) -> None:
    ctx, user, _ = await seed(session)
    group = await create_group(session, ctx, "finance")
    await add_group_member(session, ctx, group.id, user.id)
    listed = await list_groups(session, ctx)
    assert [(g.name, members) for g, members in listed] == [("finance", [user.id])]
    await remove_group_member(session, ctx, group.id, user.id)
    listed = await list_groups(session, ctx)
    assert listed[0][1] == []
    await delete_group(session, ctx, group.id)
    assert await list_groups(session, ctx) == []


async def test_membership_is_org_scoped(session: AsyncSession) -> None:
    ctx, _, _ = await seed(session)
    other_org = Organization(name="OtherOrg")
    session.add(other_org)
    await session.flush()
    outsider = User(org_id=other_org.id, email="o@other.com", password_hash="x", role="user")  # noqa: S106
    session.add(outsider)
    await session.commit()
    group = await create_group(session, ctx, "finance")
    with pytest.raises(NotFoundError):
        await add_group_member(session, ctx, group.id, outsider.id)
    other_ctx = TenantContext(
        user_id=outsider.id, org_id=other_org.id, role="admin", workspace_ids=frozenset()
    )
    with pytest.raises(NotFoundError):
        await delete_group(session, other_ctx, group.id)  # cross-org delete


async def test_tenant_context_defaults_have_no_groups() -> None:
    from uuid import uuid4

    ctx = TenantContext(user_id=uuid4(), org_id=uuid4(), role="user", workspace_ids=frozenset())
    assert ctx.group_ids == frozenset()
