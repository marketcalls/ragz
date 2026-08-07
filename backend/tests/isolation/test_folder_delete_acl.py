"""Adversarial test for RBAC-01 (folder cascade-delete ACL bypass).

Context: docs/audit/rbac-controls-role-separation-gap-analysis-2026-08-07.md
section RBAC-01. `delete_folder` (folders.py) collects every document in a
folder's subtree and used to flip them all to status="deleting" with NO
per-document ACL check, even though every default role="user" member has
documents.delete. A member who is a workspace member but NOT in a document's
ACL group could therefore delete a document they cannot even open, simply by
deleting a parent folder -- while direct DELETE /documents/{id} correctly
denies them via get_document_checked. If this test ever fails, treat it as a
security incident, not a flake (matches the isolation suite's own contract).
"""

from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ragz.core.errors import WorkspaceAccessDenied
from ragz.modules.documents import folders as folders_service
from ragz.modules.documents.models import Document, Folder
from ragz.modules.documents.service import create_from_upload, set_document_acl
from ragz.modules.tenancy.context import TenantContext
from ragz.modules.tenancy.models import Group, Workspace
from tests.isolation.conftest import seed_acl_workspace


async def _seed_folder_with_docs(
    session: AsyncSession,
) -> tuple[
    TenantContext, TenantContext, TenantContext, Workspace, Group, Folder, Document, Document
]:
    """Reuses seed_acl_workspace's ONE org / ONE workspace / insider-outsider-
    admin fixture, and puts a Finance-restricted document AND a sibling
    unrestricted document under the same folder -- so the assertions below
    can prove both "the restricted doc is protected" and "the unrestricted
    sibling isn't collaterally blocked/mutated"."""
    ctx_in, ctx_out, ctx_admin, ws, finance = await seed_acl_workspace(session)
    folder = await folders_service.create_folder(
        session, ctx_admin, ws.id, name="Finance Reports", parent_folder_id=None
    )
    restricted = await create_from_upload(
        session, ctx_admin, ws.id, filename="restricted.pdf", mime="application/pdf",
        data=b"finance secret content", folder_id=folder.id,
    )
    await set_document_acl(session, ctx_admin, restricted.id, [finance.id])
    unrestricted = await create_from_upload(
        session, ctx_admin, ws.id, filename="open.pdf", mime="application/pdf",
        data=b"public content", folder_id=folder.id,
    )
    return ctx_in, ctx_out, ctx_admin, ws, finance, folder, restricted, unrestricted


async def _document_status(session: AsyncSession, document_id: UUID) -> str:
    """A genuinely FRESH query (not a refresh of an already-loaded ORM
    object) -- proves the row itself was never written, not just that our
    in-memory handle wasn't updated."""
    return (
        await session.execute(select(Document.status).where(Document.id == document_id))
    ).scalar_one()


async def _folder_exists(session: AsyncSession, folder_id: UUID) -> bool:
    row = (
        await session.execute(select(Folder.id).where(Folder.id == folder_id))
    ).scalar_one_or_none()
    return row is not None


async def test_outsider_cannot_delete_folder_containing_restricted_doc(
    session: AsyncSession, stack_env: None,
) -> None:
    """The core RBAC-01 scenario: a role="user" member of the workspace who is
    NOT in the restricted document's ACL group must not be able to delete
    that document by deleting its parent folder."""
    _, ctx_out, _, _ws, _finance, folder, restricted, unrestricted = (
        await _seed_folder_with_docs(session)
    )

    with pytest.raises(WorkspaceAccessDenied):
        await folders_service.delete_folder(session, ctx_out, folder.id)


async def test_denied_folder_delete_mutates_nothing(
    session: AsyncSession, stack_env: None,
) -> None:
    """ATOMICITY (audit acceptance test): when authorization of one
    descendant fails, NEITHER the restricted doc NOR its unrestricted sibling
    changes status, and the folder rows still exist -- proving the whole
    operation is preflighted before any write, not partially applied."""
    _, ctx_out, _, _ws, _finance, folder, restricted, unrestricted = (
        await _seed_folder_with_docs(session)
    )

    with pytest.raises(WorkspaceAccessDenied):
        await folders_service.delete_folder(session, ctx_out, folder.id)

    assert await _document_status(session, restricted.id) != "deleting"
    assert await _document_status(session, unrestricted.id) != "deleting"
    assert await _folder_exists(session, folder.id) is True


async def test_group_member_can_delete_whole_subtree(
    session: AsyncSession, stack_env: None,
) -> None:
    """CONTROL: the check is selective, not a blanket deny. A member who IS
    in the Finance group can delete the entire subtree, restricted document
    included."""
    ctx_in, _, _, _ws, _finance, folder, restricted, unrestricted = (
        await _seed_folder_with_docs(session)
    )

    document_ids = await folders_service.delete_folder(session, ctx_in, folder.id)

    assert set(document_ids) == {restricted.id, unrestricted.id}
    assert await _document_status(session, restricted.id) == "deleting"
    assert await _document_status(session, unrestricted.id) == "deleting"
    assert await _folder_exists(session, folder.id) is False


async def test_admin_can_delete_whole_subtree(
    session: AsyncSession, stack_env: None,
) -> None:
    """CONTROL: an admin (role bypass, not group membership -- ctx_admin
    carries no groups) can also delete the entire subtree."""
    _, _, ctx_admin, _ws, _finance, folder, restricted, unrestricted = (
        await _seed_folder_with_docs(session)
    )

    document_ids = await folders_service.delete_folder(session, ctx_admin, folder.id)

    assert set(document_ids) == {restricted.id, unrestricted.id}
    assert await _document_status(session, restricted.id) == "deleting"
    assert await _document_status(session, unrestricted.id) == "deleting"
    assert await _folder_exists(session, folder.id) is False
