"""Retained security-audit proofs for auth/RBAC findings.

These tests intentionally assert the currently vulnerable behavior.  They are
evidence tests, not acceptance tests for the eventual remediation.
"""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ragz.core.config import Settings
from ragz.core.errors import AuthenticationError
from ragz.modules.audit.models import AuditEvent
from ragz.modules.auth.models import User
from ragz.modules.auth.passwords import hash_password
from ragz.modules.auth.service import login
from ragz.modules.tenancy import service as tenancy_service
from ragz.modules.tenancy.context import TenantContext, build_context_for_user
from ragz.modules.tenancy.models import Organization, RoleTemplate


async def test_org_admin_can_self_assign_sensitive_carve_outs(
    session: AsyncSession,
) -> None:
    """An org admin can grant themselves both nominally carved-out powers."""
    org = Organization(name="security-audit-self-assignment")
    session.add(org)
    await session.flush()
    admin = User(
        org_id=org.id,
        email="self-assign-admin@audit.example",
        password_hash=hash_password("correct horse battery staple"),
        role="admin",
    )
    sensitive = RoleTemplate(
        name="security-audit-sensitive-template",
        permissions=["audit.read", "documents.acl.bypass"],
        status="active",
    )
    session.add_all([admin, sensitive])
    await session.flush()
    actor_context = TenantContext(
        user_id=admin.id,
        org_id=org.id,
        role="admin",
        workspace_ids=frozenset(),
    )

    updated = await tenancy_service.assign_custom_role(
        session, actor_context, admin.id, sensitive.id
    )
    effective = await build_context_for_user(session, updated)

    assert updated.custom_role_id == sensitive.id
    assert {"audit.read", "documents.acl.bypass"} <= effective.permissions


async def test_failed_password_login_is_recorded_as_success(
    session: AsyncSession, kek_file: str
) -> None:
    """The login.failure action currently inherits AuditEvent.result='success'."""
    org = Organization(name="security-audit-login-result")
    session.add(org)
    await session.flush()
    user = User(
        org_id=org.id,
        email="login-denial@audit.example",
        password_hash=hash_password("correct horse battery staple"),
        role="user",
    )
    session.add(user)
    await session.commit()

    settings = Settings(_env_file=None, environment="test", kek_file=kek_file)
    with pytest.raises(AuthenticationError, match="invalid credentials"):
        await login(
            session,
            email=user.email,
            password="definitely-wrong",
            settings=settings,
        )

    event = (
        await session.execute(
            select(AuditEvent).where(AuditEvent.action == "login.failure")
        )
    ).scalar_one()
    assert event.result == "success"
    assert event.reason_code is None
    assert event.auth_method is None
    assert event.source_ip is None
