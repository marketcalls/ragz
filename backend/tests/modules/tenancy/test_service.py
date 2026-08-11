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


@pytest.fixture
async def member_env(session: AsyncSession) -> dict:
    """Task 11 (RBAC-08): a workspace with exactly one 'owner' and one
    'contributor' member, plus a ctx (org-tier admin, so require_action's
    guard is a non-issue -- these tests call service functions directly, not
    through the routes) scoped to it."""
    from ragz.modules.auth.models import User
    from ragz.modules.tenancy.models import Organization, WorkspaceMember

    org = Organization(name="rbac08-member-org")
    session.add(org)
    await session.flush()
    owner = User(org_id=org.id, email="owner@rbac08.example",
                 password_hash="x", role="admin")  # noqa: S106
    other = User(org_id=org.id, email="other@rbac08.example",
                 password_hash="x", role="user")  # noqa: S106
    session.add_all([owner, other])
    await session.flush()
    ws = Workspace(org_id=org.id, name="MemberWS")
    session.add(ws)
    await session.flush()
    session.add_all([
        WorkspaceMember(workspace_id=ws.id, user_id=owner.id, role="owner"),
        WorkspaceMember(workspace_id=ws.id, user_id=other.id, role="contributor"),
    ])
    await session.commit()
    ctx = TenantContext(
        user_id=owner.id, org_id=org.id, role="admin", workspace_ids=frozenset({ws.id})
    )
    return {"ctx": ctx, "ws_id": ws.id, "owner_id": owner.id, "other_id": other.id}


async def test_remove_last_owner_is_rejected(
    session: AsyncSession, member_env: dict
) -> None:
    from ragz.core.errors import ConflictError

    with pytest.raises(ConflictError):
        await service.remove_member(
            session, member_env["ctx"], member_env["ws_id"], member_env["owner_id"]
        )


async def test_change_role_of_last_owner_away_from_owner_is_rejected(
    session: AsyncSession, member_env: dict
) -> None:
    from ragz.core.errors import ConflictError

    with pytest.raises(ConflictError):
        await service.change_member_role(
            session, member_env["ctx"], member_env["ws_id"], member_env["owner_id"], "viewer"
        )


async def test_remove_member_succeeds_when_not_the_last_owner(
    session: AsyncSession, member_env: dict
) -> None:
    await service.remove_member(
        session, member_env["ctx"], member_env["ws_id"], member_env["other_id"]
    )
    members = await service.list_members(session, member_env["ctx"], member_env["ws_id"])
    assert member_env["other_id"] not in {m.user_id for m in members}


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
