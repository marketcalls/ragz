"""Scoped usage/cost reporting aggregation (design 2026-08-15 §2, Phase 2b).

Service-level (DB-only) tests for quotas/reporting.usage_report: the scope
filters, the group_by aggregation, and the estimated-$ math (token features
off the model_catalog price map; per-call features off the config rate).
Adversarial cross-user/org/department isolation lives in
tests/isolation/test_reporting_isolation.py.
"""

from datetime import timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from ragz.core.db import naive_utc
from ragz.modules.auth.models import User
from ragz.modules.models.catalog import ModelCatalogEntry
from ragz.modules.models.models import Model
from ragz.modules.quotas.models import UsageRecord
from ragz.modules.quotas.reporting import usage_report
from ragz.modules.tenancy.context import TenantContext
from ragz.modules.tenancy.models import Organization, Workspace, WorkspaceMember

INP = 1e-6
OUTP = 2e-6
RERANK = 0.01
WEB = 0.02


class _Env:
    def __init__(self, org, u1, u2, ws1, ws2, model):  # type: ignore[no-untyped-def]
        self.org, self.u1, self.u2 = org, u1, u2
        self.ws1, self.ws2, self.model = ws1, ws2, model

    def ctx(self, user: User, role: str = "user") -> TenantContext:
        return TenantContext(user_id=user.id, org_id=self.org.id, role=role,
                             workspace_ids=frozenset())


async def _seed(session: AsyncSession) -> _Env:
    org = Organization(name="RepOrg")
    session.add(org)
    await session.flush()
    u1 = User(org_id=org.id, email="u1@rep.com", password_hash="x", role="user")  # noqa: S106
    u2 = User(org_id=org.id, email="u2@rep.com", password_hash="x", role="user")  # noqa: S106
    ws1 = Workspace(org_id=org.id, name="ws1")
    ws2 = Workspace(org_id=org.id, name="ws2")
    model = Model(litellm_model_name="gpt-rep", display_name="GPT Rep",
                  provider_kind="openai")
    session.add_all([u1, u2, ws1, ws2, model])
    await session.flush()
    session.add(ModelCatalogEntry(name="gpt-rep", input_cost_per_token=INP,
                                  output_cost_per_token=OUTP))
    # u1 OWNS ws1 (its department) and is a plain contributor in ws2.
    session.add(WorkspaceMember(workspace_id=ws1.id, user_id=u1.id, role="owner"))
    session.add(WorkspaceMember(workspace_id=ws2.id, user_id=u1.id, role="contributor"))

    env = _Env(org, u1, u2, ws1, ws2, model)
    now = naive_utc()
    yesterday = now - timedelta(days=1)
    rows = [
        # u1 / ws1 / chat, two days
        _rec(env, u1, ws1, model, "chat", p=1000, c=500, when=now),
        _rec(env, u1, ws1, model, "chat", p=200, c=100, when=yesterday),
        # u2 / ws2 / chat, today
        _rec(env, u2, ws2, model, "chat", p=300, c=0, when=now),
        # u1 / ws1 / per-call features (no model), today
        _rec(env, u1, ws1, None, "rerank", units=5, when=now),
        _rec(env, u1, ws1, None, "web_search", units=2, when=now),
    ]
    session.add_all(rows)
    await session.commit()
    return env


def _rec(env: _Env, user: User, ws, model, feature, *, p=0, c=0, units=0, when=None):  # type: ignore[no-untyped-def]
    return UsageRecord(
        org_id=env.org.id, user_id=user.id,
        workspace_id=ws.id if ws is not None else None,
        model_id=model.id if model is not None else None,
        feature=feature, prompt_tokens=p, completion_tokens=c, units=units,
        created_at=when or naive_utc(),
    )


async def _report(session, env, **kw):  # type: ignore[no-untyped-def]
    return await usage_report(
        session, env.ctx(env.u1), rerank_usd_per_call=RERANK,
        web_search_usd_per_call=WEB, **kw,
    )


async def test_group_by_user_org_scope_totals_and_cost(session: AsyncSession) -> None:
    env = await _seed(session)
    rows = {r.group: r for r in
            await _report(session, env, scope="org", days=30, group_by="user")}
    assert set(rows) == {str(env.u1.id), str(env.u2.id)}

    u1 = rows[str(env.u1.id)]
    assert (u1.prompt_tokens, u1.completion_tokens, u1.units) == (1200, 600, 7)
    # chat 1200*INP + 600*OUTP = 0.0024 ; rerank 5*0.01 ; web 2*0.02
    assert u1.cost_usd == pytest.approx(0.0024 + 0.05 + 0.04)
    assert u1.by_feature["chat"].tokens == 1800
    assert u1.by_feature["chat"].cost_usd == pytest.approx(0.0024)
    assert u1.by_feature["rerank"].units == 5
    assert u1.by_feature["rerank"].cost_usd == pytest.approx(0.05)
    assert u1.by_feature["web_search"].cost_usd == pytest.approx(0.04)

    u2 = rows[str(env.u2.id)]
    assert (u2.prompt_tokens, u2.completion_tokens, u2.units) == (300, 0, 0)
    assert u2.cost_usd == pytest.approx(300 * INP)


async def test_group_by_day_org_scope(session: AsyncSession) -> None:
    env = await _seed(session)
    today = naive_utc().date().isoformat()
    yday = (naive_utc().date() - timedelta(days=1)).isoformat()
    rows = {r.group: r for r in
            await _report(session, env, scope="org", days=30, group_by="day")}
    assert set(rows) == {today, yday}
    assert (rows[today].prompt_tokens, rows[today].completion_tokens,
            rows[today].units) == (1300, 500, 7)
    assert (rows[yday].prompt_tokens, rows[yday].completion_tokens) == (200, 100)
    assert rows[yday].cost_usd == pytest.approx(200 * INP + 100 * OUTP)


async def test_group_by_feature_org_scope(session: AsyncSession) -> None:
    env = await _seed(session)
    rows = {r.group: r for r in
            await _report(session, env, scope="org", days=30, group_by="feature")}
    assert set(rows) == {"chat", "rerank", "web_search"}
    assert (rows["chat"].prompt_tokens, rows["chat"].completion_tokens) == (1500, 600)
    assert rows["chat"].cost_usd == pytest.approx(1500 * INP + 600 * OUTP)
    assert rows["rerank"].units == 5 and rows["rerank"].cost_usd == pytest.approx(0.05)
    assert rows["web_search"].cost_usd == pytest.approx(0.04)


async def test_group_by_workspace_org_scope(session: AsyncSession) -> None:
    env = await _seed(session)
    rows = {r.group: r for r in
            await _report(session, env, scope="org", days=30, group_by="workspace")}
    assert set(rows) == {str(env.ws1.id), str(env.ws2.id)}
    assert (rows[str(env.ws1.id)].prompt_tokens,
            rows[str(env.ws1.id)].completion_tokens,
            rows[str(env.ws1.id)].units) == (1200, 600, 7)
    assert rows[str(env.ws2.id)].prompt_tokens == 300


async def test_department_scope_only_managed_workspaces(session: AsyncSession) -> None:
    env = await _seed(session)
    # u1 owns ws1 only -> department is ws1, never ws2 (u1 is a plain member there).
    rows = {r.group: r for r in
            await _report(session, env, scope="department", days=30, group_by="workspace")}
    assert set(rows) == {str(env.ws1.id)}
    assert rows[str(env.ws1.id)].units == 7


async def test_department_scope_empty_when_managing_nothing(session: AsyncSession) -> None:
    env = await _seed(session)
    # u2 owns/manages no workspace -> empty department, empty report.
    rows = await usage_report(session, env.ctx(env.u2), scope="department",
                              days=30, group_by="workspace")
    assert rows == []


async def test_self_scope_and_days_window(session: AsyncSession) -> None:
    env = await _seed(session)
    # self = u1's rows only.
    rows = await _report(session, env, scope="self", days=30, group_by="day")
    totals = (sum(r.prompt_tokens for r in rows), sum(r.completion_tokens for r in rows),
              sum(r.units for r in rows))
    assert totals == (1200, 600, 7)

    # days=1 excludes yesterday's u1 chat (200/100).
    rows1 = {r.group: r for r in
             await _report(session, env, scope="self", days=1, group_by="day")}
    today = naive_utc().date().isoformat()
    assert set(rows1) == {today}
    assert (rows1[today].prompt_tokens, rows1[today].completion_tokens) == (1000, 500)


async def test_missing_catalog_price_yields_zero_token_cost(session: AsyncSession) -> None:
    """A model with no model_catalog row prices its token rows at $0 (never an
    error); per-call rows still price off the config rate."""
    org = Organization(name="NoPriceOrg")
    session.add(org)
    await session.flush()
    user = User(org_id=org.id, email="np@x.com", password_hash="x", role="user")  # noqa: S106
    ws = Workspace(org_id=org.id, name="w")
    model = Model(litellm_model_name="uncatalogued", display_name="U",
                  provider_kind="openai")
    session.add_all([user, ws, model])
    await session.flush()
    session.add_all([
        UsageRecord(org_id=org.id, user_id=user.id, workspace_id=ws.id,
                    model_id=model.id, feature="chat", prompt_tokens=1000,
                    completion_tokens=0, units=0),
        UsageRecord(org_id=org.id, user_id=user.id, workspace_id=ws.id,
                    model_id=None, feature="web_search", prompt_tokens=0,
                    completion_tokens=0, units=3),
    ])
    await session.commit()
    ctx = TenantContext(user_id=user.id, org_id=org.id, role="user",
                        workspace_ids=frozenset())
    rows = {r.group: r for r in await usage_report(
        session, ctx, scope="org", days=30, group_by="feature",
        web_search_usd_per_call=WEB)}
    assert rows["chat"].cost_usd == 0.0  # no catalog price -> $0
    assert rows["web_search"].cost_usd == pytest.approx(3 * WEB)


async def test_org_scope_never_crosses_org(session: AsyncSession) -> None:
    env = await _seed(session)
    # A second org with its own usage the u1 org-scope report must never see.
    other = Organization(name="OtherOrg")
    session.add(other)
    await session.flush()
    other_user = User(org_id=other.id, email="o@other.com", password_hash="x",  # noqa: S106
                      role="user")
    session.add(other_user)
    await session.flush()
    session.add(UsageRecord(org_id=other.id, user_id=other_user.id, model_id=None,
                            feature="chat", prompt_tokens=9999, completion_tokens=0))
    await session.commit()
    rows = {r.group: r for r in
            await _report(session, env, scope="org", days=30, group_by="user")}
    assert str(other_user.id) not in rows
    assert all(r.prompt_tokens != 9999 for r in rows.values())


async def test_unknown_group_by_rejected(session: AsyncSession) -> None:
    from ragz.core.errors import BadRequestError

    env = await _seed(session)
    with pytest.raises(BadRequestError):
        await usage_report(session, env.ctx(env.u1), scope="org", days=30,
                           group_by="nonsense")
    with pytest.raises(BadRequestError):
        await usage_report(session, env.ctx(env.u1), scope="nonsense", days=30,
                           group_by="day")


async def test_platform_scope_spans_all_orgs(session: AsyncSession) -> None:
    """platform scope (route-gated to superadmin) drops the org filter and sees
    every org's rows."""
    env = await _seed(session)
    other = Organization(name="PlatOther")
    session.add(other)
    await session.flush()
    ou = User(org_id=other.id, email="po@x.com", password_hash="x", role="user")  # noqa: S106
    session.add(ou)
    await session.flush()
    session.add(UsageRecord(org_id=other.id, user_id=ou.id, model_id=None,
                            feature="chat", prompt_tokens=42, completion_tokens=0))
    await session.commit()
    rows = {r.group: r for r in await usage_report(
        session, env.ctx(env.u1, role="superadmin"), scope="platform",
        days=30, group_by="user")}
    assert str(env.u1.id) in rows  # org RepOrg
    assert str(ou.id) in rows      # org PlatOther -- cross-org, platform sees it
