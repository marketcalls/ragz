from datetime import datetime
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from ragz.core.errors import NotFoundError, QuotaExceeded
from ragz.modules.auth.models import User
from ragz.modules.quotas.service import (
    check_quota,
    get_usage_status,
    period_bounds,
    record_usage,
    set_org_quota,
    set_user_quota,
)
from ragz.modules.tenancy.context import TenantContext
from ragz.modules.tenancy.models import Organization


def test_period_bounds_clamps_and_wraps() -> None:
    start, end = period_bounds(datetime(2026, 2, 10), reset_day=31)
    assert (start, end) == (datetime(2026, 1, 31), datetime(2026, 2, 28))
    start, end = period_bounds(datetime(2026, 2, 28), reset_day=31)
    assert (start, end) == (datetime(2026, 2, 28), datetime(2026, 3, 31))
    start, end = period_bounds(datetime(2026, 12, 15), reset_day=1)
    assert (start, end) == (datetime(2026, 12, 1), datetime(2027, 1, 1))


async def _seed(session: AsyncSession) -> tuple[TenantContext, TenantContext, User]:
    org = Organization(name="QOrg")
    session.add(org)
    await session.flush()
    user = User(org_id=org.id, email="q@qorg.com", password_hash="x", role="user")  # noqa: S106
    admin = User(org_id=org.id, email="qa@qorg.com", password_hash="x", role="admin")  # noqa: S106
    session.add_all([user, admin])
    await session.commit()
    admin_ctx = TenantContext(user_id=admin.id, org_id=org.id, role="admin",
                              workspace_ids=frozenset())
    super_ctx = TenantContext(user_id=admin.id, org_id=org.id, role="superadmin",
                              workspace_ids=frozenset())
    return admin_ctx, super_ctx, user


async def test_unlimited_when_no_quota_rows(session: AsyncSession) -> None:
    admin_ctx, _, user = await _seed(session)
    await record_usage(session, org_id=admin_ctx.org_id, user_id=user.id, model_id=None,
                       feature="chat", prompt_tokens=100, completion_tokens=50)
    status = await get_usage_status(session, None, org_id=admin_ctx.org_id, user_id=user.id)
    assert status.used_tokens == 150
    assert status.allocated_tokens is None
    assert not status.warning and not status.blocked
    await check_quota(session, None, org_id=admin_ctx.org_id, user_id=user.id)  # no raise


async def test_warning_and_block_thresholds(session: AsyncSession) -> None:
    admin_ctx, super_ctx, user = await _seed(session)
    await set_org_quota(session, super_ctx, admin_ctx.org_id,
                        monthly_tokens=10_000, default_user_monthly_tokens=1_000, reset_day=1)
    await record_usage(session, org_id=admin_ctx.org_id, user_id=user.id, model_id=None,
                       feature="chat", prompt_tokens=800, completion_tokens=0)
    status = await get_usage_status(session, None, org_id=admin_ctx.org_id, user_id=user.id)
    assert status.allocated_tokens == 1_000 and status.warning and not status.blocked

    await record_usage(session, org_id=admin_ctx.org_id, user_id=user.id, model_id=None,
                       feature="chat", prompt_tokens=200, completion_tokens=0)
    with pytest.raises(QuotaExceeded) as exc:
        await check_quota(session, None, org_id=admin_ctx.org_id, user_id=user.id)
    assert "resets" in exc.value.detail


async def test_user_override_beats_org_default(session: AsyncSession) -> None:
    admin_ctx, super_ctx, user = await _seed(session)
    await set_org_quota(session, super_ctx, admin_ctx.org_id,
                        monthly_tokens=10_000, default_user_monthly_tokens=100, reset_day=1)
    await set_user_quota(session, admin_ctx, user.id, 5_000)
    status = await get_usage_status(session, None, org_id=admin_ctx.org_id, user_id=user.id)
    assert status.allocated_tokens == 5_000
    await set_user_quota(session, admin_ctx, user.id, None)  # back to org default
    status = await get_usage_status(session, None, org_id=admin_ctx.org_id, user_id=user.id)
    assert status.allocated_tokens == 100


async def test_org_ceiling_blocks_even_unlimited_user(session: AsyncSession) -> None:
    admin_ctx, super_ctx, user = await _seed(session)
    await set_org_quota(session, super_ctx, admin_ctx.org_id,
                        monthly_tokens=500, default_user_monthly_tokens=None, reset_day=1)
    await record_usage(session, org_id=admin_ctx.org_id, user_id=user.id, model_id=None,
                       feature="ingestion", prompt_tokens=600, completion_tokens=0)
    with pytest.raises(QuotaExceeded):
        await check_quota(session, None, org_id=admin_ctx.org_id, user_id=user.id)


async def test_user_quota_is_org_scoped(session: AsyncSession) -> None:
    admin_ctx, _, _ = await _seed(session)
    with pytest.raises(NotFoundError):
        await set_user_quota(session, admin_ctx, uuid4(), 100)
