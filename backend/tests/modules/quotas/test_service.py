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


# --- Cost reporting (design 2026-08-15 §2): per-call `units` ------------------


async def test_units_persist_and_never_count_as_tokens(session: AsyncSession) -> None:
    """A per-call feature records `units` but 0 tokens; a token feature records
    tokens but 0 units. Every token aggregation must ignore units entirely --
    units are calls, not tokens, and must never inflate a token budget."""
    from sqlalchemy import select

    from ragz.modules.quotas.models import UsageRecord
    from ragz.modules.quotas.service import daily_usage

    admin_ctx, _, user = await _seed(session)
    await record_usage(session, org_id=admin_ctx.org_id, user_id=user.id, model_id=None,
                       feature="rerank", prompt_tokens=0, completion_tokens=0, units=7)
    await record_usage(session, org_id=admin_ctx.org_id, user_id=user.id, model_id=None,
                       feature="web_search", prompt_tokens=0, completion_tokens=0, units=1)
    await record_usage(session, org_id=admin_ctx.org_id, user_id=user.id, model_id=None,
                       feature="chat", prompt_tokens=120, completion_tokens=30)

    rerank_row = (
        await session.execute(select(UsageRecord).where(UsageRecord.feature == "rerank"))
    ).scalar_one()
    assert rerank_row.units == 7
    assert rerank_row.prompt_tokens == 0 and rerank_row.completion_tokens == 0

    # get_usage_status / _sum_since count ONLY prompt+completion tokens (150),
    # never the 8 units.
    status = await get_usage_status(session, None, org_id=admin_ctx.org_id, user_id=user.id)
    assert status.used_tokens == 150
    assert status.org_used_tokens == 150

    # daily_usage aggregates the same token columns -- units excluded there too.
    points = await daily_usage(session, user_id=user.id, days=1)
    assert sum(p.prompt_tokens + p.completion_tokens for p in points) == 150


# --- Cost reporting: pricing helper (quotas/costing.py) -----------------------


def test_token_cost_and_per_call_cost() -> None:
    from ragz.modules.quotas.costing import per_call_cost, token_cost

    assert token_cost(
        1000, 500, input_cost_per_token=1e-6, output_cost_per_token=2e-6
    ) == pytest.approx(0.001 + 0.001)
    # A missing catalog rate contributes 0, never an error.
    assert token_cost(
        1000, 500, input_cost_per_token=None, output_cost_per_token=None
    ) == 0.0
    assert per_call_cost(3, usd_per_call=0.01) == pytest.approx(0.03)
    assert per_call_cost(0, usd_per_call=0.01) == 0.0


def test_estimate_cost_dispatches_on_feature() -> None:
    from ragz.modules.quotas.costing import estimate_cost

    # Token features price off tokens x catalog rate.
    assert estimate_cost(
        "embedding", prompt_tokens=1000, input_cost_per_token=1e-6
    ) == pytest.approx(0.001)
    # Per-call features price off units x config rate, ignoring any tokens.
    assert estimate_cost(
        "rerank", units=4, prompt_tokens=999, usd_per_call=0.002
    ) == pytest.approx(0.008)
    assert estimate_cost(
        "web_search", units=2, usd_per_call=0.01
    ) == pytest.approx(0.02)
    # Rate defaults to 0 -> $ hidden for a per-call feature without a config rate.
    assert estimate_cost("web_search", units=5) == 0.0
