"""Adversarial isolation tests for folders (iron rule 1).

Folders (backend/src/ragz/modules/documents/folders.py) are a new
org-owned table, and delete_folder is the single most destructive bulk
operation this feature adds -- a cascade over an entire folder subtree that
flips every document found to status="deleting". If any test here fails,
treat it as a security incident, not a flake.
"""

from uuid import uuid4

import httpx
import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ragz.modules.auth.models import User
from ragz.modules.auth.passwords import hash_password
from ragz.modules.documents import folders as folders_service
from ragz.modules.documents.models import Document
from ragz.modules.documents.service import create_from_upload
from ragz.modules.tenancy import service as tenancy_service
from ragz.modules.tenancy.context import TenantContext
from ragz.modules.tenancy.models import Organization, Workspace
from tests.api.test_folders_routes import auth
from tests.conftest import assign_contributor_role


@pytest.fixture
async def org_b_admin(session: AsyncSession) -> User:
    """A second, unrelated org's admin -- the adversarial cross-org caller."""
    org = Organization(name="isoFolderRival")
    session.add(org)
    await session.flush()
    user = User(org_id=org.id, email="rival@isofolder.com",
               password_hash=hash_password("pw123456"), role="admin")
    session.add(user)
    await session.commit()
    return user


async def test_cross_org_folder_access_returns_404_on_every_route(
    client: httpx.AsyncClient, seeded_user: User, org_b_admin: User, session: AsyncSession,
) -> None:
    """Existence must never leak (matching every other org-owned resource in
    this suite): org B's admin, hitting org A's folder id on the
    delete-preview (GET), patch (PATCH), and delete (DELETE) routes, must
    get the exact same 404 a nonexistent id would -- never a 403, which
    would confirm the id refers to something real."""
    h_a = await auth(client, seeded_user.email)
    h_b = await auth(client, org_b_admin.email)

    r = await client.post("/api/v1/workspaces", json={"name": "Finance"}, headers=h_a)
    assert r.status_code == 201
    ws_id = r.json()["id"]
    r = await client.post(
        f"/api/v1/workspaces/{ws_id}/folders",
        json={"name": "Legal", "parent_folder_id": None}, headers=h_a,
    )
    assert r.status_code == 201
    folder_id = r.json()["id"]

    r = await client.get(f"/api/v1/folders/{folder_id}/delete-preview", headers=h_b)
    assert r.status_code == 404
    assert r.headers["content-type"] == "application/problem+json"

    r = await client.patch(
        f"/api/v1/folders/{folder_id}", json={"name": "Hijacked"}, headers=h_b
    )
    assert r.status_code == 404

    r = await client.delete(f"/api/v1/folders/{folder_id}", headers=h_b)
    assert r.status_code == 404

    # Not a vacuous pass: org A's own admin still operates on it fine, and
    # the delete never actually ran (the row is untouched by org B's calls).
    r = await client.get(f"/api/v1/folders/{folder_id}/delete-preview", headers=h_a)
    assert r.status_code == 200
    assert r.json() == {"document_count": 0, "subfolder_count": 0}


async def test_cross_workspace_folder_access_denied_for_plain_member(
    client: httpx.AsyncClient, seeded_user: User, session: AsyncSession,
) -> None:
    """Same org, two workspaces -- the real product-leak scenario (two teams
    sharing one tenant). A plain 'user'-role member of ws1 ONLY must not
    reach a folder that actually lives in ws2, even though both workspaces
    share an org_id: get_folder_checked's ctx.workspace_ids membership check
    (not just org_id) has to hold for folders exactly like it already does
    for every other org-owned resource in this suite. Unlike the cross-org
    case above this is a 403 (WorkspaceAccessDenied), not a 404 -- the
    folder genuinely IS in the caller's own org, just a workspace they don't
    belong to -- mirroring get_folder_checked's own status-code split. This
    is a different angle than the existing unit-level
    test_get_folder_checked_rejects_cross_workspace_parent (which pins
    create_folder's PARENT validation for an admin caller): here a
    non-admin member directly hits an EXISTING folder's own routes."""
    h_admin = await auth(client, seeded_user.email)

    r = await client.post("/api/v1/workspaces", json={"name": "WS1"}, headers=h_admin)
    assert r.status_code == 201
    ws1_id = r.json()["id"]
    r = await client.post("/api/v1/workspaces", json={"name": "WS2"}, headers=h_admin)
    assert r.status_code == 201
    ws2_id = r.json()["id"]

    r = await client.post(
        f"/api/v1/workspaces/{ws2_id}/folders",
        json={"name": "Secret", "parent_folder_id": None}, headers=h_admin,
    )
    assert r.status_code == 201
    folder_id = r.json()["id"]

    member = User(org_id=seeded_user.org_id, email="member@acme.com",
                 password_hash=hash_password("pw123456"), role="user")  # noqa: S106
    session.add(member)
    await session.flush()
    # RBAC-04: the folder routes are gated on documents.upload/delete
    # (UploadDep/DeleteDep), no longer in the read-only default. Grant the
    # member full contributor rights so the cross-workspace 403s below are
    # decided by the WORKSPACE-membership check (the isolation property under
    # test) rather than by a missing permission -- and so the "not vacuous"
    # own-workspace create at the end still succeeds.
    await assign_contributor_role(session, member)
    await session.commit()
    r = await client.post(
        f"/api/v1/workspaces/{ws1_id}/members",
        json={"user_id": str(member.id), "role": "contributor"}, headers=h_admin,
    )
    assert r.status_code == 204

    h_member = await auth(client, member.email)
    r = await client.get(f"/api/v1/folders/{folder_id}/delete-preview", headers=h_member)
    assert r.status_code == 403

    r = await client.patch(
        f"/api/v1/folders/{folder_id}", json={"name": "Hijacked"}, headers=h_member
    )
    assert r.status_code == 403

    r = await client.delete(f"/api/v1/folders/{folder_id}", headers=h_member)
    assert r.status_code == 403

    # Not a vacuous pass: the same member CAN operate within their own
    # workspace membership.
    r = await client.post(
        f"/api/v1/workspaces/{ws1_id}/folders",
        json={"name": "Own", "parent_folder_id": None}, headers=h_member,
    )
    assert r.status_code == 201


async def test_subtree_delete_query_guard_rejects_forged_cross_tenant_document(
    session: AsyncSession, stack_env: None,
) -> None:
    """Regression test for the Finding-1 hardening: prove the QUERY-LEVEL
    workspace_id guard added to _collect_subtree_folder_ids/count_subtree/
    delete_folder actually defends, not merely that the normal API can't
    construct a cross-tenant folder/document relationship (it can't --
    create_from_upload validates folder_id via get_folder_checked(...,
    workspace_id=ws.id), so this scenario is otherwise unreachable through
    any sanctioned code path).

    The adversarial row is built directly via the ORM, bypassing that
    service-layer invariant entirely. Two forgeries are exercised, because
    f5a8d2e91c47 moved half of this guarantee down into the schema:

    1. CROSS-ORG (org B document -> org A folder) is now impossible to
       construct at all. The composite FK fk_documents_folder_id_org pairs
       (folder_id, org_id) against folders(id, org_id), so the insert is
       rejected by Postgres. This test previously asserted the opposite --
       that "nothing at the DB layer stops this" -- which was true before
       that migration and is now false. Asserting the rejection keeps the
       constraint honest: if someone weakens the FK back to single-column,
       this fails.
    2. CROSS-WORKSPACE within ONE org is still constructible, because the
       composite FK pairs org_id only -- workspace_id is not part of it.
       That is precisely the case the query-level guard exists for. Before
       the Finding-1 fix, `Document.folder_id.in_(folder_ids)` ALONE would
       have matched this row on any count_subtree/delete_folder call against
       another workspace's folder, letting its admin count and destroy it.
       The added `Document.workspace_id == workspace_id` predicate must
       exclude it."""
    org_a = Organization(name="isoFolderGuardA")
    org_b = Organization(name="isoFolderGuardB")
    session.add_all([org_a, org_b])
    await session.flush()
    user_a = User(org_id=org_a.id, email="a@isofolderguard.com",
                 password_hash="x", role="admin")  # noqa: S106
    user_b = User(org_id=org_b.id, email="b@isofolderguard.com",
                 password_hash="x", role="admin")  # noqa: S106
    session.add_all([user_a, user_b])
    await session.flush()
    ctx_a = TenantContext(
        user_id=user_a.id, org_id=org_a.id, role="admin", workspace_ids=frozenset()
    )

    ws_a = await tenancy_service.create_workspace(session, ctx_a, "ws-a")
    ws_b = Workspace(org_id=org_b.id, name="ws-b")
    session.add(ws_b)
    await session.flush()

    folder_a = await folders_service.create_folder(
        session, ctx_a, ws_a.id, name="LegalA", parent_folder_id=None
    )

    # The legitimate, same-tenant document that SHOULD be caught by the
    # subtree walk -- proves the guard isn't just vacuously excluding
    # everything.
    own_doc = await create_from_upload(
        session, ctx_a, ws_a.id, filename="own.pdf", mime="application/pdf",
        data=b"own", folder_id=folder_a.id,
    )

    # Forgery 1 -- CROSS-ORG. org B's document pointed at org A's folder.
    # The composite FK must reject this outright. Wrapped in a SAVEPOINT so
    # the expected IntegrityError doesn't poison the setup built above.
    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            session.add(Document(
                org_id=org_b.id, workspace_id=ws_b.id, filename="leak.pdf",
                mime="application/pdf", size_bytes=3, content_hash="deadbeef",
                storage_key="leak-key", created_by=user_b.id, lineage_id=uuid4(),
                folder_id=folder_a.id,
            ))

    # Forgery 2 -- CROSS-WORKSPACE inside org A. The composite FK permits this
    # (same org), so the query-level workspace guard is the ONLY thing standing
    # between a second workspace's document and org A's folder cascade.
    ws_a2 = Workspace(org_id=org_a.id, name="ws-a2")
    session.add(ws_a2)
    await session.flush()
    cross_tenant_doc = Document(
        org_id=org_a.id, workspace_id=ws_a2.id, filename="leak.pdf", mime="application/pdf",
        size_bytes=3, content_hash="deadbeef", storage_key="leak-key",
        created_by=user_a.id, lineage_id=uuid4(), folder_id=folder_a.id,
    )
    session.add(cross_tenant_doc)
    await session.commit()

    document_count, _subfolder_count = await folders_service.count_subtree(
        session, ctx_a, folder_a.id
    )
    assert document_count == 1  # own_doc only -- the forged row must NOT be counted

    document_ids = await folders_service.delete_folder(session, ctx_a, folder_a.id)
    assert document_ids == [own_doc.id]  # the forged row was never selected for deletion

    await session.refresh(cross_tenant_doc)
    # Untouched: never flipped to "deleting", so ws-a's admin can never get
    # it enqueue_delete'd. (Its folder_id may have been nulled by the DB's
    # own ondelete=SET NULL (folder_id) action once folder_a's row was removed
    # -- that FK fires for ANY referencing row regardless of workspace, and is
    # harmless: it only clears an already-invalid cross-workspace reference,
    # and the column list keeps org_id intact. The security property that
    # matters is the status/deletion outcome below.)
    assert cross_tenant_doc.status == "queued"
