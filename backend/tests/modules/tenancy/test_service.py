"""Plan J Task 12 (§6): the retrieval-settings-change eval trigger inside
update_retrieval_settings. Local ctx/ws fixtures mirror
tests/modules/evals/conftest.py's seed_workspace pattern (role="admin" so the
fixture ctx can call update_retrieval_settings without a WorkspacePatch/route
in the way)."""

from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from ragz.core.errors import AuthorizationError
from ragz.modules.evals import service as evals_service
from ragz.modules.outbox import service as outbox_service
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
    # The trigger is now an OUTBOX event published inside the caller's
    # transaction, not a local import of worker.tasks.enqueue_eval_run -- which
    # is why tenancy.service no longer needs a layering exception at all. The
    # assertion is on the durable record of intent rather than on a fire-and-
    # forget call, so it also proves the eval survives a broker outage.
    await service.update_retrieval_settings(session, ctx, ws.id, {"top_k": 12})
    await session.commit()

    events = await outbox_service.claim_due(session)
    assert [(e.topic, e.payload["workspace_id"], e.payload["triggered_by"]) for e in events] == [
        ("evals.run", str(ws.id), "settings_change")
    ]


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


async def test_admin_cannot_mutate_multi_query_or_sibling_setting(
    session: AsyncSession, ctx: TenantContext, ws: Workspace
) -> None:
    original_top_k = ws.top_k

    with pytest.raises(AuthorizationError, match="requires superadmin"):
        await service.update_retrieval_settings(
            session,
            ctx,
            ws.id,
            {"top_k": 12, "multi_query_enabled": True},
        )

    await session.refresh(ws)
    assert ws.top_k == original_top_k
    assert ws.multi_query_enabled is False


async def test_default_model_assignment_rejects_disabled_and_embedding_models(
    session: AsyncSession, ctx: TenantContext, ws: Workspace
) -> None:
    from ragz.core.errors import NotFoundError
    from ragz.modules.models.models import Model

    disabled = Model(
        litellm_model_name="disabled-chat-default",
        display_name="Disabled chat",
        provider_kind="ollama",
        modality="chat",
        enabled=False,
    )
    embedding = Model(
        litellm_model_name="embedding-default",
        display_name="Embedding",
        provider_kind="tei",
        modality="embedding",
        dimension=8,
        collection_name="embedding-default",
    )
    session.add_all([disabled, embedding])
    await session.commit()

    for model in (disabled, embedding):
        with pytest.raises(NotFoundError):
            await service.set_default_model(session, ctx, ws.id, model.id)
        await session.refresh(ws)
        assert ws.default_model_id is None


async def test_admin_without_independent_authority_cannot_grant_sensitive_role(
    session: AsyncSession,
) -> None:
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
    # RBAC-09: assign_custom_role now rejects a non-active template; this
    # test is about the admin-target relaxation, not the lifecycle gate, so
    # construct it already active.
    template = RoleTemplate(
        name="cm-admin-target", permissions=["documents.acl.bypass"], status="active"
    )
    session.add(template)
    await session.flush()
    seeded_ctx = TenantContext(
        user_id=actor.id, org_id=org.id, role="admin", workspace_ids=frozenset()
    )
    with pytest.raises(AuthorizationError, match="sensitive role grant"):
        await service.assign_custom_role(session, seeded_ctx, target.id, template.id)
    await session.refresh(target)
    assert target.custom_role_id is None


async def test_delegated_sensitive_grantor_can_grant_to_another_same_org_admin(
    session: AsyncSession,
) -> None:
    from ragz.modules.auth.models import User
    from ragz.modules.tenancy.models import Organization, RoleTemplate

    org = Organization(name="delegated-sensitive-grant-org")
    session.add(org)
    await session.flush()
    actor = User(
        org_id=org.id,
        email="grantor@assign.example",
        password_hash="x",  # noqa: S106
        role="admin",
    )
    target = User(
        org_id=org.id,
        email="grantee@assign.example",
        password_hash="x",  # noqa: S106
        role="admin",
    )
    sensitive = RoleTemplate(
        name="delegated-audit-reader",
        permissions=["audit.read"],
        status="active",
    )
    session.add_all([actor, target, sensitive])
    await session.flush()
    grantor_ctx = TenantContext(
        user_id=actor.id,
        org_id=org.id,
        role="admin",
        workspace_ids=frozenset(),
        permissions=frozenset({"roles.sensitive.assign"}),
    )

    updated = await service.assign_custom_role(
        session, grantor_ctx, target.id, sensitive.id
    )

    assert updated.custom_role_id == sensitive.id


async def test_sensitive_grantor_still_cannot_grant_to_self(session: AsyncSession) -> None:
    from ragz.modules.auth.models import User
    from ragz.modules.tenancy.models import Organization, RoleTemplate

    org = Organization(name="sensitive-self-grant-org")
    session.add(org)
    await session.flush()
    actor = User(
        org_id=org.id,
        email="self-grantor@assign.example",
        password_hash="x",  # noqa: S106
        role="admin",
    )
    sensitive = RoleTemplate(
        name="self-sensitive-template",
        permissions=["documents.acl.bypass"],
        status="active",
    )
    session.add_all([actor, sensitive])
    await session.flush()
    grantor_ctx = TenantContext(
        user_id=actor.id,
        org_id=org.id,
        role="admin",
        workspace_ids=frozenset(),
        permissions=frozenset({"roles.sensitive.assign"}),
    )

    with pytest.raises(AuthorizationError, match="self-grant"):
        await service.assign_custom_role(session, grantor_ctx, actor.id, sensitive.id)


async def test_superadmin_can_bootstrap_cross_org_sensitive_grantor(
    session: AsyncSession,
) -> None:
    from ragz.modules.auth.models import User
    from ragz.modules.tenancy.models import Organization, RoleTemplate

    platform = Organization(name="sensitive-platform-org")
    tenant = Organization(name="sensitive-tenant-org")
    session.add_all([platform, tenant])
    await session.flush()
    superadmin = User(
        org_id=platform.id,
        email="bootstrap-superadmin@assign.example",
        password_hash="x",  # noqa: S106
        role="superadmin",
    )
    target = User(
        org_id=tenant.id,
        email="bootstrap-grantor@assign.example",
        password_hash="x",  # noqa: S106
        role="admin",
    )
    grantor = RoleTemplate(
        name="bootstrap-sensitive-grantor",
        permissions=["roles.sensitive.assign"],
        status="active",
    )
    session.add_all([superadmin, target, grantor])
    await session.flush()
    super_ctx = TenantContext(
        user_id=superadmin.id,
        org_id=platform.id,
        role="superadmin",
        workspace_ids=frozenset(),
    )

    updated = await service.assign_custom_role(session, super_ctx, target.id, grantor.id)

    assert updated.custom_role_id == grantor.id


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


@pytest.fixture
async def seeded_ctx(session: AsyncSession) -> TenantContext:
    """Task 17 (RBAC-09): a plain admin ctx for role-template lifecycle
    tests (create/activate/impact)."""
    from ragz.modules.auth.models import User
    from ragz.modules.tenancy.models import Organization

    org = Organization(name="role-template-versioning-org")
    session.add(org)
    await session.flush()
    actor = User(org_id=org.id, email="actor@versioning.example",
                 password_hash="x", role="admin")  # noqa: S106
    session.add(actor)
    await session.commit()
    return TenantContext(user_id=actor.id, org_id=org.id, role="admin", workspace_ids=frozenset())


@pytest.fixture
async def user_id(session: AsyncSession, seeded_ctx: TenantContext) -> UUID:
    """A same-org 'user'-tier account to assign templates to."""
    from ragz.modules.auth.models import User

    user = User(org_id=seeded_ctx.org_id, email="target@versioning.example",
                password_hash="x", role="user")  # noqa: S106
    session.add(user)
    await session.commit()
    return user.id


async def test_new_template_starts_as_draft_and_cannot_be_assigned(
    session: AsyncSession, seeded_ctx: TenantContext, user_id: UUID
) -> None:
    from ragz.core.errors import ConflictError

    template = await service.create_role_template(
        session, seeded_ctx, name="draft-test", description="", permissions=["chat.read"],
    )
    assert template.status == "draft"
    with pytest.raises(ConflictError):
        await service.assign_custom_role(session, seeded_ctx, user_id, template.id)


async def test_activate_makes_it_assignable_and_bumps_version(
    session: AsyncSession, seeded_ctx: TenantContext, user_id: UUID
) -> None:
    template = await service.create_role_template(
        session, seeded_ctx, name="activate-test", description="", permissions=["chat.read"],
    )
    activated = await service.activate_role_template(session, seeded_ctx, template.id)
    assert activated.status == "active" and activated.version == 2
    await service.assign_custom_role(session, seeded_ctx, user_id, template.id)  # no longer raises


async def test_impact_preview_counts_assigned_users(
    session: AsyncSession, seeded_ctx: TenantContext, user_id: UUID
) -> None:
    template = await service.create_role_template(
        session, seeded_ctx, name="impact-test", description="", permissions=["chat.read"],
    )
    await service.activate_role_template(session, seeded_ctx, template.id)
    assert await service.role_template_impact(session, template.id) == 0
    await service.assign_custom_role(session, seeded_ctx, user_id, template.id)
    assert await service.role_template_impact(session, template.id) == 1


async def test_activate_snapshots_a_version_row(
    session: AsyncSession, seeded_ctx: TenantContext
) -> None:
    from sqlalchemy import select as sa_select

    from ragz.modules.tenancy.models import RoleTemplateVersion

    template = await service.create_role_template(
        session, seeded_ctx, name="snap-test", description="", permissions=["chat.read"],
    )
    await service.activate_role_template(session, seeded_ctx, template.id)
    rows = list((await session.execute(
        sa_select(RoleTemplateVersion).where(RoleTemplateVersion.role_template_id == template.id)
    )).scalars())
    assert len(rows) == 1 and rows[0].permissions == ["chat.read"]


async def test_rollback_restores_previous_permissions_as_a_new_version(
    session: AsyncSession, seeded_ctx: TenantContext
) -> None:
    template = await service.create_role_template(
        session, seeded_ctx, name="rollback-test", description="", permissions=["chat.read"],
    )
    await service.activate_role_template(session, seeded_ctx, template.id)  # v2, snapshot #1
    await service.update_role_template(
        session, seeded_ctx, template.id, permissions=["chat.read", "documents.upload"],
    )
    await service.activate_role_template(session, seeded_ctx, template.id)  # v3, snapshot #2
    rolled_back = await service.rollback_role_template(session, seeded_ctx, template.id)
    assert rolled_back.permissions == ["chat.read"]  # snapshot #1's permissions restored
    assert rolled_back.version == 4  # rollback is a NEW forward version, not a rewrite


async def test_rollback_with_no_previous_version_conflicts(
    session: AsyncSession, seeded_ctx: TenantContext
) -> None:
    from ragz.core.errors import ConflictError

    template = await service.create_role_template(
        session, seeded_ctx, name="rollback-none-test", description="", permissions=["chat.read"],
    )
    await service.activate_role_template(session, seeded_ctx, template.id)  # v2, snapshot #1 only
    with pytest.raises(ConflictError):
        await service.rollback_role_template(session, seeded_ctx, template.id)
