"""RBAC'd usage/cost reporting (design 2026-08-15 §2, Phase 2b).

Both routes share a single floor -- require_action("reports.view.self") -- so
every caller must at least hold the self-reporting grant (members do by
default; admins/superadmin bypass). The PER-SCOPE escalation is enforced
in-handler by `_require_scope`:

    self       -> reports.view.self       (floor; anyone here already holds it)
    department -> reports.view.department  (auto-held by admins)
    org        -> reports.view.org         (auto-held by admins)
    platform   -> ctx.role == "superadmin" ONLY

`platform` is deliberately NOT gated on the reports.view.platform action: an
org admin auto-holds every non-carve-out action (see tenancy/context.py), so
trusting that action would leak cross-org data to org admins. It is gated on
the concrete superadmin role instead.
"""

import re
from collections.abc import AsyncIterator
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ragz.api.deps import get_session
from ragz.core.config import Settings, get_settings
from ragz.core.errors import AuthorizationError
from ragz.modules.quotas.reporting import ReportRow, usage_report
from ragz.modules.tenancy.context import TenantContext, require_action

router = APIRouter(prefix="/reports", tags=["reports"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
# The reporting FLOOR: everyone reaching either handler holds reports.view.self
# (or is admin/superadmin). Wider scopes escalate in-handler via _require_scope.
ReportsFloorDep = Annotated[TenantContext, Depends(require_action("reports.view.self"))]

Scope = Literal["self", "department", "org", "platform"]
GroupBy = Literal["day", "user", "workspace", "feature", "model"]

# Cells whose first character a spreadsheet could read as a formula lead-in are
# prefixed with a single quote to force text (mirrors frontend audit/csv.ts).
_FORMULA_LEAD_IN = re.compile(r"^[=+\-@\t\r]")
_CSV_SPECIAL = re.compile(r'[",\n]')


class FeatureBreakdownOut(BaseModel):
    tokens: int
    units: int
    cost_usd: float


class ReportRowOut(BaseModel):
    group: str
    prompt_tokens: int
    completion_tokens: int
    units: int
    cost_usd: float
    by_feature: dict[str, FeatureBreakdownOut]


class ReportPageOut(BaseModel):
    rows: list[ReportRowOut]
    scope: Scope
    days: int
    group_by: GroupBy


def _require_scope(ctx: TenantContext, scope: str) -> None:
    """Per-scope escalation on top of the reports.view.self floor. Superadmin
    bypasses the action check (except it IS the only thing that satisfies
    platform); every other scope accepts its matching action, which admins
    auto-hold."""
    if scope == "platform":
        # Superadmin-only, by ROLE -- never by the auto-granted action.
        if ctx.role != "superadmin":
            raise AuthorizationError("platform-scope reports require superadmin")
        return
    action = {
        "self": "reports.view.self",
        "department": "reports.view.department",
        "org": "reports.view.org",
    }[scope]
    if ctx.role != "superadmin" and action not in ctx.permissions:
        raise AuthorizationError(f"requires permission {action}")


async def _rows(
    session: AsyncSession, ctx: TenantContext, settings: Settings,
    *, scope: str, days: int, group_by: str,
) -> list[ReportRow]:
    _require_scope(ctx, scope)
    return await usage_report(
        session, ctx, scope=scope, days=days, group_by=group_by,
        rerank_usd_per_call=settings.rerank_usd_per_call,
        web_search_usd_per_call=settings.web_search_usd_per_call,
    )


@router.get("/usage", response_model=ReportPageOut)
async def get_usage_report(
    session: SessionDep,
    ctx: ReportsFloorDep,
    settings: SettingsDep,
    scope: Scope = "self",
    days: Annotated[int, Query(ge=1, le=36500)] = 30,
    group_by: GroupBy = "day",
) -> ReportPageOut:
    rows = await _rows(session, ctx, settings, scope=scope, days=days, group_by=group_by)
    return ReportPageOut(
        rows=[
            ReportRowOut(
                group=r.group,
                prompt_tokens=r.prompt_tokens,
                completion_tokens=r.completion_tokens,
                units=r.units,
                cost_usd=r.cost_usd,
                by_feature={
                    feat: FeatureBreakdownOut(
                        tokens=b.tokens, units=b.units, cost_usd=b.cost_usd
                    )
                    for feat, b in r.by_feature.items()
                },
            )
            for r in rows
        ],
        scope=scope,
        days=days,
        group_by=group_by,
    )


def _csv_cell(value: str) -> str:
    s = f"'{value}" if _FORMULA_LEAD_IN.match(value) else value
    if _CSV_SPECIAL.search(s):
        s = '"' + s.replace('"', '""') + '"'
    return s


async def _csv_lines(rows: list[ReportRow]) -> AsyncIterator[str]:
    yield "group,prompt_tokens,completion_tokens,units,cost_usd\n"
    for r in rows:
        cells = [
            _csv_cell(r.group),
            str(r.prompt_tokens),
            str(r.completion_tokens),
            str(r.units),
            f"{r.cost_usd:.6f}",
        ]
        yield ",".join(cells) + "\n"


@router.get("/usage/export")
async def export_usage_report(
    session: SessionDep,
    ctx: ReportsFloorDep,
    settings: SettingsDep,
    scope: Scope = "self",
    days: Annotated[int, Query(ge=1, le=36500)] = 30,
    group_by: GroupBy = "day",
) -> StreamingResponse:
    rows = await _rows(session, ctx, settings, scope=scope, days=days, group_by=group_by)
    return StreamingResponse(
        _csv_lines(rows),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="usage-report.csv"'},
    )
