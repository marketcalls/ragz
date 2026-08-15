"""Scoped usage/cost reporting (design 2026-08-15 §2, Phase 2b).

Reads the indexed `usage_records` ledger directly (no rollup table -- see
quotas/service.py's module docstring) and rolls each row up into a per-group
`ReportRow` carrying token/units totals plus estimated $ (quotas.costing).

DB-only and HTTP-free: the route (api/routes/reports.py) owns scope RBAC and
serialization. Every query is org-scoped (iron rule 1) EXCEPT the
superadmin-only `platform` scope, which the route gates on ctx.role before
ever calling in. Cost is computed off a once-loaded price map -- never on a
hot path (this is a read endpoint).
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ragz.core.db import naive_utc
from ragz.core.errors import BadRequestError
from ragz.modules.models.catalog import ModelCatalogEntry
from ragz.modules.models.models import Model
from ragz.modules.quotas.costing import PER_CALL_FEATURES, estimate_cost
from ragz.modules.quotas.models import UsageRecord
from ragz.modules.tenancy.context import TenantContext
from ragz.modules.tenancy.models import WorkspaceMember

SCOPES = frozenset({"self", "department", "org", "platform"})
GROUP_BYS = frozenset({"day", "user", "workspace", "feature", "model"})


@dataclass(frozen=True)
class FeatureBreakdown:
    tokens: int
    units: int
    cost_usd: float


@dataclass(frozen=True)
class ReportRow:
    group: str
    prompt_tokens: int
    completion_tokens: int
    units: int
    cost_usd: float
    by_feature: dict[str, FeatureBreakdown]


@dataclass
class _Acc:
    prompt: int = 0
    completion: int = 0
    units: int = 0
    cost: float = 0.0
    # feature -> [tokens, units, cost]
    by_feature: dict[str, list[float]] = field(default_factory=dict)


def _group_column(group_by: str) -> Any:
    if group_by == "day":
        return func.date_trunc("day", UsageRecord.created_at)
    if group_by == "user":
        return UsageRecord.user_id
    if group_by == "workspace":
        return UsageRecord.workspace_id
    if group_by == "feature":
        return UsageRecord.feature
    if group_by == "model":
        return UsageRecord.model_id
    raise BadRequestError(f"unknown group_by {group_by!r}")


def _group_str(value: object) -> str:
    if value is None:
        return "unattributed"
    if isinstance(value, datetime):
        return value.date().isoformat()
    return str(value)


async def _managed_workspace_ids(
    session: AsyncSession, ctx: TenantContext
) -> frozenset[UUID]:
    """The workspaces the caller OWNS/MANAGES -- the department scope's reach.
    Department Admin = the owner/manager WorkspaceMember role (design decision
    1/3). A caller who manages no workspace has an empty department."""
    return frozenset(
        (
            await session.execute(
                select(WorkspaceMember.workspace_id).where(
                    WorkspaceMember.user_id == ctx.user_id,
                    WorkspaceMember.role.in_(("owner", "manager")),
                )
            )
        )
        .scalars()
        .all()
    )


async def _scope_filters(
    session: AsyncSession, ctx: TenantContext, scope: str
) -> list[ColumnElement[bool]] | None:
    """Returns the WHERE predicates for `scope`, or None to signal an
    empty result (a department caller who manages nothing). Every scope but
    `platform` pins org_id (iron rule 1); `platform` is superadmin-only and
    the route gates it before we get here."""
    if scope == "self":
        return [UsageRecord.user_id == ctx.user_id, UsageRecord.org_id == ctx.org_id]
    if scope == "department":
        managed = await _managed_workspace_ids(session, ctx)
        if not managed:
            return None
        return [
            UsageRecord.org_id == ctx.org_id,
            UsageRecord.workspace_id.in_(managed),
        ]
    if scope == "org":
        return [UsageRecord.org_id == ctx.org_id]
    if scope == "platform":
        return []
    raise BadRequestError(f"unknown scope {scope!r}")


async def _price_map(
    session: AsyncSession,
) -> dict[UUID | None, tuple[float | None, float | None]]:
    """model_id -> (input_cost_per_token, output_cost_per_token), joining the
    (global) model registry to the catalog on litellm_model_name. A model with
    no catalog match yields (None, None) -> $0 for its token rows. `models` is
    not org-owned, so this cross-org load carries no tenancy concern."""
    rows = (
        await session.execute(
            select(
                Model.id,
                ModelCatalogEntry.input_cost_per_token,
                ModelCatalogEntry.output_cost_per_token,
            ).outerjoin(
                ModelCatalogEntry, ModelCatalogEntry.name == Model.litellm_model_name
            )
        )
    ).all()
    return {mid: (inp, outp) for mid, inp, outp in rows}


async def usage_report(
    session: AsyncSession,
    ctx: TenantContext,
    *,
    scope: str,
    days: int,
    group_by: str,
    rerank_usd_per_call: float = 0.0,
    web_search_usd_per_call: float = 0.0,
) -> list[ReportRow]:
    """Scoped, grouped usage + estimated-$ report over the trailing `days`
    window (start = midnight `days-1` ago). Aggregates in SQL by
    (group_key, feature, model_id), then rolls the (feature, model) rows up
    into one `ReportRow` per group_key with token/units totals, a per-feature
    breakdown, and estimated $ (token features off the catalog price map;
    per-call features off the config rate). Rows are ordered by group_key."""
    if group_by not in GROUP_BYS:
        raise BadRequestError(f"unknown group_by {group_by!r}")
    filters = await _scope_filters(session, ctx, scope)
    if filters is None:
        return []

    today = naive_utc().date()
    start_date = today - timedelta(days=days - 1)
    start = datetime(start_date.year, start_date.month, start_date.day)

    group_col = _group_column(group_by)
    stmt = (
        select(
            group_col.label("g"),
            UsageRecord.feature,
            UsageRecord.model_id,
            func.coalesce(func.sum(UsageRecord.prompt_tokens), 0),
            func.coalesce(func.sum(UsageRecord.completion_tokens), 0),
            func.coalesce(func.sum(UsageRecord.units), 0),
            func.count(),
        )
        .where(UsageRecord.created_at >= start, *filters)
        .group_by(group_col, UsageRecord.feature, UsageRecord.model_id)
        .order_by(group_col)
    )
    rows = (await session.execute(stmt)).all()

    prices = await _price_map(session) if rows else {}

    def _per_call_rate(feature: str) -> float:
        if feature == "rerank":
            return rerank_usd_per_call
        if feature == "web_search":
            return web_search_usd_per_call
        return 0.0

    acc: dict[str, _Acc] = {}
    for g, feature, model_id, prompt, completion, units, _count in rows:
        prompt, completion, units = int(prompt), int(completion), int(units)
        if feature in PER_CALL_FEATURES:
            cost = estimate_cost(
                feature, units=units, usd_per_call=_per_call_rate(feature)
            )
        else:
            inp, outp = prices.get(model_id, (None, None))
            cost = estimate_cost(
                feature,
                prompt_tokens=prompt,
                completion_tokens=completion,
                input_cost_per_token=inp,
                output_cost_per_token=outp,
            )
        key = _group_str(g)
        a = acc.get(key)
        if a is None:
            a = acc[key] = _Acc()
        a.prompt += prompt
        a.completion += completion
        a.units += units
        a.cost += cost
        fb = a.by_feature.setdefault(feature, [0, 0, 0.0])
        fb[0] += prompt + completion
        fb[1] += units
        fb[2] += cost

    return [
        ReportRow(
            group=key,
            prompt_tokens=a.prompt,
            completion_tokens=a.completion,
            units=a.units,
            cost_usd=a.cost,
            by_feature={
                feat: FeatureBreakdown(
                    tokens=int(vals[0]), units=int(vals[1]), cost_usd=vals[2]
                )
                for feat, vals in a.by_feature.items()
            },
        )
        for key, a in acc.items()
    ]
