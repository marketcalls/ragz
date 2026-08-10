"""Plan J Task 12 (§6): the retrieval-settings-change eval trigger inside
update_retrieval_settings. Local ctx/ws fixtures mirror
tests/modules/evals/conftest.py's seed_workspace pattern (role="admin" so the
fixture ctx can call update_retrieval_settings without a WorkspacePatch/route
in the way)."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from ragz.modules.evals import service as evals_service
from ragz.modules.tenancy import service
from ragz.modules.tenancy.context import TenantContext
from ragz.modules.tenancy.models import Workspace
from tests.modules.retrieval.test_retrieve import seed_workspace


@pytest.fixture
async def ctx_ws(
    session: AsyncSession, stack_env: None
) -> tuple[TenantContext, Workspace]:
    return await seed_workspace(session, "tenancy-settings", role="admin")


@pytest.fixture
async def ctx(ctx_ws: tuple[TenantContext, Workspace]) -> TenantContext:
    return ctx_ws[0]


@pytest.fixture
async def ws(ctx_ws: tuple[TenantContext, Workspace]) -> Workspace:
    return ctx_ws[1]


async def test_top_k_change_triggers_eval_run_when_golden_queries_exist(
    session: AsyncSession, ctx: TenantContext, ws: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    await evals_service.create_golden_query(
        session, ctx, ws.id, question="q", expected_document_ids=[]
    )
    enqueued: list[tuple[object, str]] = []
    # enqueue_eval_run is imported locally inside update_retrieval_settings
    # (avoids a real circular import -- evals.service already imports
    # tenancy.service at module scope), so the interception point is the
    # worker.tasks module attribute itself, not a tenancy.service name --
    # mirrors tests/modules/documents/test_versioning.py's enqueue_reindex
    # patching for the same reason.
    monkeypatch.setattr(
        "ragz.worker.tasks.enqueue_eval_run",
        lambda workspace_id, triggered_by: enqueued.append((workspace_id, triggered_by)),
    )
    await service.update_retrieval_settings(session, ctx, ws.id, {"top_k": 12})
    assert enqueued == [(ws.id, "settings_change")]


async def test_fallback_policy_change_does_not_trigger_eval_run(
    session: AsyncSession, ctx: TenantContext, ws: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """fallback_policy doesn't affect retrieval ranking -- only
    top_k/min_score/rerank_enabled do."""
    enqueued: list[tuple[object, str]] = []
    monkeypatch.setattr(
        "ragz.worker.tasks.enqueue_eval_run",
        lambda workspace_id, triggered_by: enqueued.append((workspace_id, triggered_by)),
    )
    await service.update_retrieval_settings(session, ctx, ws.id, {"fallback_policy": "decline"})
    assert enqueued == []


async def test_assign_custom_role_now_allows_admin_target(session: AsyncSession) -> None:
    """RBAC-05: an org admin is now a valid assign_custom_role target -- an
    admin needs an EXPLICIT template (e.g. Content Manager) for content-ACL
    bypass, exactly as a 'user'-tier account needs one for upload/delete.
    Before this change an admin target 409'd."""
    from ragz.modules.auth.models import User
    from ragz.modules.tenancy.models import Organization, RoleTemplate

    org = Organization(name="assign-admin-target-org")
    session.add(org)
    await session.flush()
    actor = User(org_id=org.id, email="actor@assign.example",
                 password_hash="x", role="admin")  # noqa: S106
    target = User(org_id=org.id, email="target@assign.example",
                  password_hash="x", role="admin")  # noqa: S106
    session.add_all([actor, target])
    await session.flush()
    template = RoleTemplate(name="cm-admin-target", permissions=["documents.acl.bypass"])
    session.add(template)
    await session.flush()
    seeded_ctx = TenantContext(
        user_id=actor.id, org_id=org.id, role="admin", workspace_ids=frozenset()
    )
    updated = await service.assign_custom_role(session, seeded_ctx, target.id, template.id)
    assert updated.custom_role_id == template.id


async def test_assign_custom_role_still_rejects_superadmin_target(session: AsyncSession) -> None:
    """RBAC-05: a superadmin target is still rejected (platform-tier, out of
    this org-scoped mechanism's reach) -- 404 so existence never leaks."""
    from ragz.core.errors import NotFoundError
    from ragz.modules.auth.models import User
    from ragz.modules.tenancy.models import Organization

    org = Organization(name="assign-superadmin-target-org")
    session.add(org)
    await session.flush()
    actor = User(org_id=org.id, email="actor2@assign.example",
                 password_hash="x", role="admin")  # noqa: S106
    superadmin = User(org_id=org.id, email="sa@assign.example",
                      password_hash="x", role="superadmin")  # noqa: S106
    session.add_all([actor, superadmin])
    await session.flush()
    seeded_ctx = TenantContext(
        user_id=actor.id, org_id=org.id, role="admin", workspace_ids=frozenset()
    )
    with pytest.raises(NotFoundError):
        await service.assign_custom_role(session, seeded_ctx, superadmin.id, None)
