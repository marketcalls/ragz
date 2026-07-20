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
from datetime import datetime, timedelta
from uuid import UUID

from redis.asyncio import Redis
from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from raghub.core.db import naive_utc
from raghub.core.errors import NotFoundError, QuotaExceeded
from raghub.modules.audit.service import record_audit
from raghub.modules.auth.models import User
from raghub.modules.chat.models import Chat, Message
from raghub.modules.models.models import Model
from raghub.modules.quotas.models import OrgQuota, UsageRecord, UserQuota
from raghub.modules.quotas.schemas import UserQuotaOut
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


async def get_org_quota(session: AsyncSession, org_id: UUID) -> OrgQuota | None:
    return (
        await session.execute(select(OrgQuota).where(OrgQuota.org_id == org_id))
    ).scalar_one_or_none()


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


async def org_member_effective_allocations(
    session: AsyncSession, org_id: UUID
) -> dict[UUID, int | None]:
    """Every org member's effective monthly allocation right now: their
    UserQuota override if one exists, else the org's current default. Used by
    the org-quota route to re-mirror gateway budgets (a stale mirror otherwise
    hard-blocks a user whose allocation was just raised) -- callers filter this
    down to users who actually hold a vkey before touching the gateway."""
    org_quota = (
        await session.execute(select(OrgQuota).where(OrgQuota.org_id == org_id))
    ).scalar_one_or_none()
    default_tokens = org_quota.default_user_monthly_tokens if org_quota is not None else None
    member_ids = (
        await session.execute(select(User.id).where(User.org_id == org_id))
    ).scalars().all()
    if not member_ids:
        return {}
    override_rows = (
        await session.execute(
            select(UserQuota.user_id, UserQuota.monthly_tokens)
            .where(UserQuota.user_id.in_(member_ids))
        )
    ).all()
    overrides: dict[UUID, int] = {uid: tokens for uid, tokens in override_rows}
    return {uid: overrides.get(uid, default_tokens) for uid in member_ids}


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


async def get_user_quota_with_usage(
    session: AsyncSession, redis: Redis | None, ctx: TenantContext, user_id: UUID
) -> UserQuotaOut:
    """Org-scopes the target user exactly like set_user_quota; the usage half
    is a plain new caller of the existing get_usage_status aggregation (no
    new usage-aggregation logic)."""
    user = (
        await session.execute(
            select(User).where(User.id == user_id, User.org_id == ctx.org_id)
        )
    ).scalar_one_or_none()
    if user is None:
        raise NotFoundError("user not found")
    override = (
        await session.execute(select(UserQuota).where(UserQuota.user_id == user_id))
    ).scalar_one_or_none()
    status = await get_usage_status(session, redis, org_id=ctx.org_id, user_id=user_id)
    return UserQuotaOut(
        user_id=user_id,
        monthly_tokens=override.monthly_tokens if override else None,
        used_tokens=status.used_tokens, allocated_tokens=status.allocated_tokens,
        resets_at=status.resets_at,
    )


async def org_usage_summary(
    session: AsyncSession, *, org_id: UUID, days: int
) -> dict[str, object]:
    """Aggregates for the admin dashboard (ADM-4/QUOTA-7 groundwork; Plan G
    charts these). Straight indexed-ledger group-bys — see module docstring
    for the no-rollup-table decision."""
    since = naive_utc() - timedelta(days=days)
    base = (UsageRecord.org_id == org_id, UsageRecord.created_at >= since)
    day_col = func.date_trunc("day", UsageRecord.created_at)
    by_day = (
        await session.execute(
            select(day_col, func.sum(_TOKENS)).where(*base).group_by(day_col).order_by(day_col)
        )
    ).all()
    by_model = (
        await session.execute(
            select(UsageRecord.model_id, func.sum(_TOKENS))
            .where(*base).group_by(UsageRecord.model_id)
            .order_by(func.sum(_TOKENS).desc())
        )
    ).all()
    by_user = (
        await session.execute(
            select(UsageRecord.user_id, User.email, func.sum(_TOKENS), func.count())
            .join(User, User.id == UsageRecord.user_id)
            .where(*base).group_by(UsageRecord.user_id, User.email)
            .order_by(func.sum(_TOKENS).desc()).limit(10)
        )
    ).all()
    queries_per_day = (
        await session.execute(
            select(day_col, func.count()).where(*base).group_by(day_col).order_by(day_col)
        )
    ).all()
    model_name = func.coalesce(Model.display_name, "unattributed")
    tokens_by_model_day = (
        await session.execute(
            select(day_col, model_name, func.sum(_TOKENS))
            .join(Model, Model.id == UsageRecord.model_id, isouter=True)
            .where(*base)
            .group_by(day_col, model_name)
            .order_by(day_col)
        )
    ).all()
    kpi_queries, kpi_tokens, kpi_active_users = (
        await session.execute(
            select(
                func.count(), func.coalesce(func.sum(_TOKENS), 0),
                func.count(func.distinct(UsageRecord.user_id)),
            ).where(*base)
        )
    ).one()
    no_answer_count = (
        await session.execute(
            select(func.count()).select_from(Message)
            .join(Chat, Chat.id == Message.chat_id)
            .where(Chat.org_id == org_id, Message.no_answer.is_(True),
                   Message.created_at >= since)
        )
    ).scalar_one()
    return {
        "by_day": [{"day": d.date(), "tokens": int(t)} for d, t in by_day],
        "by_model": [{"model_id": m, "tokens": int(t)} for m, t in by_model],
        "by_user": [
            {"user_id": u, "email": e, "tokens": int(t), "queries": int(q)}
            for u, e, t, q in by_user
        ],
        "kpis": {
            "queries": int(kpi_queries),
            "total_tokens": int(kpi_tokens),
            "active_users": int(kpi_active_users),
            "no_answer_count": int(no_answer_count),
        },
        "queries_per_day": [{"day": d.date(), "count": int(c)} for d, c in queries_per_day],
        "tokens_by_model_per_day": [
            {"day": d.date(), "model_name": m, "tokens": int(t)}
            for d, m, t in tokens_by_model_day
        ],
    }


async def platform_usage_by_org(
    session: AsyncSession, *, days: int = 30
) -> list[dict[str, object]]:
    """Cross-org totals over a UNIFORM trailing window (per-org reset days would
    make rows incomparable side by side). SUP-3 groundwork."""
    from raghub.modules.tenancy.models import Organization

    since = naive_utc() - timedelta(days=days)
    rows = (
        await session.execute(
            select(UsageRecord.org_id, Organization.name, func.sum(_TOKENS))
            .join(Organization, Organization.id == UsageRecord.org_id)
            .where(UsageRecord.created_at >= since)
            .group_by(UsageRecord.org_id, Organization.name)
            .order_by(func.sum(_TOKENS).desc())
        )
    ).all()
    return [{"org_id": o, "name": n, "tokens": int(t)} for o, n, t in rows]
