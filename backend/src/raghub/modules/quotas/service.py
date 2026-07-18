"""Quotas + usage ledger (QUOTA-1/3/5).

Enforcement is dual (QUOTA-3): this module's pre-flight check backed by a
60-second Redis cache of the period aggregate (a burst can overshoot by at
most one cache window — accepted; per-user LiteLLM virtual-key budgets in
modules/models/keys.py are the hard gateway backstop), plus typed 429s so the
UI can show the reset date. Reporting reads the indexed ledger directly — no
rollup table until Plan G's load tests demand one.
"""

from calendar import monthrange
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from redis.asyncio import Redis
from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from raghub.core.db import naive_utc
from raghub.core.errors import NotFoundError, QuotaExceeded
from raghub.modules.audit.service import record_audit
from raghub.modules.auth.models import User
from raghub.modules.quotas.models import OrgQuota, UsageRecord, UserQuota
from raghub.modules.tenancy.context import TenantContext

_CACHE_TTL_SECONDS = 60
_WARN_RATIO = 0.8


def _clamped(year: int, month: int, day: int) -> datetime:
    return datetime(year, month, min(day, monthrange(year, month)[1]))


def period_bounds(now: datetime, reset_day: int) -> tuple[datetime, datetime]:
    """(period_start, next_reset), naive UTC. reset_day clamps to month length."""
    this_month = _clamped(now.year, now.month, reset_day)
    if now >= this_month:
        nxt = (now.year + 1, 1) if now.month == 12 else (now.year, now.month + 1)
        return this_month, _clamped(nxt[0], nxt[1], reset_day)
    prev = (now.year - 1, 12) if now.month == 1 else (now.year, now.month - 1)
    return _clamped(prev[0], prev[1], reset_day), this_month


async def record_usage(
    session: AsyncSession,
    *,
    org_id: UUID,
    user_id: UUID,
    model_id: UUID | None,
    feature: str,
    prompt_tokens: int,
    completion_tokens: int,
) -> None:
    session.add(
        UsageRecord(
            org_id=org_id, user_id=user_id, model_id=model_id, feature=feature,
            prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
        )
    )
    await session.commit()


_TOKENS = UsageRecord.prompt_tokens + UsageRecord.completion_tokens


async def _sum_since(
    session: AsyncSession, start: datetime, *, org_id: UUID | None, user_id: UUID | None
) -> int:
    stmt = select(func.coalesce(func.sum(_TOKENS), 0)).where(UsageRecord.created_at >= start)
    if org_id is not None:
        stmt = stmt.where(UsageRecord.org_id == org_id)
    if user_id is not None:
        stmt = stmt.where(UsageRecord.user_id == user_id)
    return int((await session.execute(stmt)).scalar_one())


async def _cached_sum(
    session: AsyncSession, redis: Redis | None, key: str, start: datetime,
    *, org_id: UUID | None, user_id: UUID | None,
) -> int:
    if redis is not None:
        cached = await redis.get(key)
        if cached is not None:
            return int(cached)
    total = await _sum_since(session, start, org_id=org_id, user_id=user_id)
    if redis is not None:
        await redis.set(key, total, ex=_CACHE_TTL_SECONDS)
    return total


@dataclass(frozen=True)
class UsageStatus:
    used_tokens: int
    allocated_tokens: int | None
    org_used_tokens: int
    org_allocated_tokens: int | None
    resets_at: datetime
    warning: bool
    blocked: bool


async def get_usage_status(
    session: AsyncSession, redis: Redis | None, *, org_id: UUID, user_id: UUID
) -> UsageStatus:
    org_quota = (
        await session.execute(select(OrgQuota).where(OrgQuota.org_id == org_id))
    ).scalar_one_or_none()
    reset_day = org_quota.reset_day if org_quota is not None else 1
    start, resets_at = period_bounds(naive_utc(), reset_day)
    period = start.date().isoformat()
    used = await _cached_sum(session, redis, f"quota:user:{user_id}:{period}", start,
                             org_id=None, user_id=user_id)
    org_used = await _cached_sum(session, redis, f"quota:org:{org_id}:{period}", start,
                                 org_id=org_id, user_id=None)
    user_quota = (
        await session.execute(select(UserQuota).where(UserQuota.user_id == user_id))
    ).scalar_one_or_none()
    allocated = (
        user_quota.monthly_tokens if user_quota is not None
        else org_quota.default_user_monthly_tokens if org_quota is not None
        else None
    )
    org_allocated = org_quota.monthly_tokens if org_quota is not None else None

    def _ratio(u: int, a: int | None) -> float:
        return u / a if a else 0.0

    warning = max(_ratio(used, allocated), _ratio(org_used, org_allocated)) >= _WARN_RATIO
    blocked = (allocated is not None and used >= allocated) or (
        org_allocated is not None and org_used >= org_allocated
    )
    return UsageStatus(
        used_tokens=used, allocated_tokens=allocated,
        org_used_tokens=org_used, org_allocated_tokens=org_allocated,
        resets_at=resets_at, warning=warning, blocked=blocked,
    )


async def check_quota(
    session: AsyncSession, redis: Redis | None, *, org_id: UUID, user_id: UUID
) -> None:
    """Pre-flight (QUOTA-3): raises a typed 429 carrying the reset date."""
    status = await get_usage_status(session, redis, org_id=org_id, user_id=user_id)
    if status.blocked:
        raise QuotaExceeded(
            f"monthly token quota exhausted; resets {status.resets_at.date().isoformat()}"
        )


async def set_org_quota(
    session: AsyncSession,
    ctx: TenantContext,
    org_id: UUID,
    *,
    monthly_tokens: int,
    default_user_monthly_tokens: int | None,
    reset_day: int,
) -> OrgQuota:
    row = (
        await session.execute(select(OrgQuota).where(OrgQuota.org_id == org_id))
    ).scalar_one_or_none()
    if row is None:
        row = OrgQuota(org_id=org_id, monthly_tokens=monthly_tokens,
                       default_user_monthly_tokens=default_user_monthly_tokens,
                       reset_day=reset_day)
        session.add(row)
    else:
        row.monthly_tokens = monthly_tokens
        row.default_user_monthly_tokens = default_user_monthly_tokens
        row.reset_day = reset_day
    await record_audit(session, org_id=org_id, actor_id=ctx.user_id,
                       action="quota.org_set", target_type="organization",
                       target_id=str(org_id))
    await session.commit()
    return row


async def set_user_quota(
    session: AsyncSession, ctx: TenantContext, user_id: UUID, monthly_tokens: int | None
) -> None:
    user = (
        await session.execute(
            select(User).where(User.id == user_id, User.org_id == ctx.org_id)
        )
    ).scalar_one_or_none()
    if user is None:
        raise NotFoundError("user not found")
    if monthly_tokens is None:
        await session.execute(sa_delete(UserQuota).where(UserQuota.user_id == user_id))
    else:
        row = (
            await session.execute(select(UserQuota).where(UserQuota.user_id == user_id))
        ).scalar_one_or_none()
        if row is None:
            session.add(UserQuota(user_id=user_id, monthly_tokens=monthly_tokens))
        else:
            row.monthly_tokens = monthly_tokens
    await record_audit(session, org_id=ctx.org_id, actor_id=ctx.user_id,
                       action="quota.user_set", target_type="user", target_id=str(user_id))
    await session.commit()
