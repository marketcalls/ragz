"""Red-team tier scaffolding (Phase 3 Plan J, Task 13): off by default.

Gated exactly like `RAGZ_TEST_OCR` (tests/integration/test_ocr_e2e.py, J-C16):
REDTEAM_ENABLED is read once at import time, and every red-team test module
applies `pytestmark = pytest.mark.skipif(not REDTEAM_ENABLED, ...)` so the
whole tier is SKIPPED (not run, not failed) in the default `pytest tests`
sweep. This tier EXTENDS tests/isolation/ (adversarial injection/evasion/
fabrication probes) - it never replaces or weakens that suite.
"""

import os
from collections.abc import Iterator
from dataclasses import dataclass

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from ragz.modules.auth.models import User
from ragz.modules.auth.passwords import hash_password
from ragz.modules.tenancy.context import TenantContext
from ragz.modules.tenancy.models import Organization, Workspace, WorkspaceMember

REDTEAM_ENABLED = bool(os.environ.get("REDTEAM"))

# Shared login password for redteam_env's admin user (mirrors tests/conftest.py's
# seeded_user convention) so probes needing an authenticated API session can
# reuse tests/api/test_chat_stream.py's `auth()` helper unmodified.
REDTEAM_ADMIN_PASSWORD = "pw123456"  # noqa: S105


@dataclass
class RedteamEnv:
    """One org/workspace/admin-user/member-user, strict_mode=True and
    fallback_policy="general_knowledge" on the workspace (the widest-surface
    configuration - every probe in this tier wants both the strict-mode
    Gatekeeper path and the general-knowledge fallback path reachable).

    Unpacks as `ctx, ws = redteam_env` (admin ctx, workspace) for probes that
    only need document/session-level access, mirroring
    tests/modules/retrieval/test_retrieve.py::seed_workspace's shape; the
    richer fields (member_ctx, admin_user, admin_password) are available by
    attribute for probes that need a second identity or an API login.
    """

    ctx: TenantContext
    ws: Workspace
    member_ctx: TenantContext
    admin_user: User
    member_user: User
    admin_password: str

    def __iter__(self) -> Iterator[object]:
        yield self.ctx
        yield self.ws


@pytest.fixture
async def redteam_env(session: AsyncSession, qdrant_collection: None) -> RedteamEnv:
    """Depends on qdrant_collection (-> stack_env) so RAGZ_DATABASE_URL/
    RAGZ_QDRANT_URL/RAGZ_MINIO_* point at the test containers BEFORE any
    probe calls ingest_text - without it, documents/ingest.py's pipeline
    stages (each opening their own session via ambient settings) silently
    connect to a different database than the one `session` just committed
    to, and every ingest_text call fails with "document ... no longer
    exists". Mirrors tests/isolation/conftest.py::two_orgs's same dependency.
    """
    org = Organization(name="redteamOrg")
    session.add(org)
    await session.flush()
    ws = Workspace(
        org_id=org.id, name="redteamWS",
        strict_mode=True, fallback_policy="general_knowledge",
    )
    admin_user = User(
        org_id=org.id, email="admin@redteamorg.com",
        password_hash=hash_password(REDTEAM_ADMIN_PASSWORD), role="admin",
    )
    member_user = User(
        org_id=org.id, email="member@redteamorg.com",
        password_hash="x", role="user",  # noqa: S106 -- never logged in, no real login needed
    )
    session.add_all([ws, admin_user, member_user])
    await session.flush()
    session.add(WorkspaceMember(workspace_id=ws.id, user_id=admin_user.id))
    session.add(WorkspaceMember(workspace_id=ws.id, user_id=member_user.id))
    await session.commit()
    ctx = TenantContext(
        user_id=admin_user.id, org_id=org.id, role="admin",
        workspace_ids=frozenset({ws.id}),
    )
    member_ctx = TenantContext(
        user_id=member_user.id, org_id=org.id, role="user",
        workspace_ids=frozenset({ws.id}),
    )
    return RedteamEnv(
        ctx=ctx, ws=ws, member_ctx=member_ctx,
        admin_user=admin_user, member_user=member_user,
        admin_password=REDTEAM_ADMIN_PASSWORD,
    )
