import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from ragz.core.db import build_session_factory
from ragz.core.errors import ConflictError, NotFoundError
from ragz.modules.auth.models import User
from ragz.modules.documents import folders as folders_service
from ragz.modules.documents.models import Folder
from ragz.modules.documents.service import create_from_upload
from ragz.modules.tenancy import service as tenancy_service
from ragz.modules.tenancy.context import TenantContext
from ragz.modules.tenancy.models import Organization


@pytest.fixture
async def ctx(session: AsyncSession) -> TenantContext:
    """Real Organization + User rows (Workspace.org_id and Folder.created_by
    FK to them) -- role="admin" so get_workspace_checked/get_folder_checked
    never need explicit workspace membership, mirroring
    tests/modules/documents/test_reembed.py's identically-shaped fixture."""
    org = Organization(name="folders-org")
    session.add(org)
    await session.flush()
    user = User(
        org_id=org.id, email="folders@test.com", password_hash="x",  # noqa: S106
        role="admin",
    )
    session.add(user)
    await session.flush()
    return TenantContext(
        user_id=user.id, org_id=org.id, role="admin",
        workspace_ids=frozenset(), group_ids=frozenset(),
    )


async def test_create_and_list_folders(session: AsyncSession, ctx: TenantContext) -> None:
    ws = await tenancy_service.create_workspace(session, ctx, "test-ws")
    root = await folders_service.create_folder(
        session, ctx, ws.id, name="Legal", parent_folder_id=None
    )
    child = await folders_service.create_folder(
        session, ctx, ws.id, name="Contracts", parent_folder_id=root.id
    )
    folders = await folders_service.list_folders(session, ctx, ws.id)
    assert {f.id for f in folders} == {root.id, child.id}


async def test_duplicate_root_folder_name_rejected(
    session: AsyncSession, ctx: TenantContext
) -> None:
    ws = await tenancy_service.create_workspace(session, ctx, "test-ws")
    await folders_service.create_folder(
        session, ctx, ws.id, name="Legal", parent_folder_id=None
    )
    with pytest.raises(IntegrityError):
        await folders_service.create_folder(
            session, ctx, ws.id, name="Legal", parent_folder_id=None
        )


async def test_same_name_at_different_levels_allowed(
    session: AsyncSession, ctx: TenantContext
) -> None:
    ws = await tenancy_service.create_workspace(session, ctx, "test-ws")
    a = await folders_service.create_folder(
        session, ctx, ws.id, name="A", parent_folder_id=None
    )
    b = await folders_service.create_folder(
        session, ctx, ws.id, name="B", parent_folder_id=None
    )
    await folders_service.create_folder(
        session, ctx, ws.id, name="Contracts", parent_folder_id=a.id
    )
    await folders_service.create_folder(
        session, ctx, ws.id, name="Contracts", parent_folder_id=b.id
    )
    folders = await folders_service.list_folders(session, ctx, ws.id)
    assert len([f for f in folders if f.name == "Contracts"]) == 2


async def test_rename_folder(session: AsyncSession, ctx: TenantContext) -> None:
    ws = await tenancy_service.create_workspace(session, ctx, "test-ws")
    folder = await folders_service.create_folder(
        session, ctx, ws.id, name="Legal", parent_folder_id=None
    )
    updated = await folders_service.rename_or_move_folder(
        session, ctx, folder.id, name="Legal Docs", parent_folder_id=None, fields_set={"name"},
    )
    assert updated.name == "Legal Docs"
    assert updated.parent_folder_id is None


async def test_move_folder_into_itself_rejected(
    session: AsyncSession, ctx: TenantContext
) -> None:
    ws = await tenancy_service.create_workspace(session, ctx, "test-ws")
    folder = await folders_service.create_folder(
        session, ctx, ws.id, name="Legal", parent_folder_id=None
    )
    with pytest.raises(ConflictError):
        await folders_service.rename_or_move_folder(
            session, ctx, folder.id, name=None, parent_folder_id=folder.id,
            fields_set={"parent_folder_id"},
        )


async def test_move_folder_into_own_descendant_rejected(
    session: AsyncSession, ctx: TenantContext
) -> None:
    ws = await tenancy_service.create_workspace(session, ctx, "test-ws")
    parent = await folders_service.create_folder(
        session, ctx, ws.id, name="Legal", parent_folder_id=None
    )
    child = await folders_service.create_folder(
        session, ctx, ws.id, name="Contracts", parent_folder_id=parent.id
    )
    with pytest.raises(ConflictError):
        await folders_service.rename_or_move_folder(
            session, ctx, parent.id, name=None, parent_folder_id=child.id,
            fields_set={"parent_folder_id"},
        )


async def test_move_folder_to_new_valid_parent(session: AsyncSession, ctx: TenantContext) -> None:
    ws = await tenancy_service.create_workspace(session, ctx, "test-ws")
    a = await folders_service.create_folder(
        session, ctx, ws.id, name="A", parent_folder_id=None
    )
    b = await folders_service.create_folder(
        session, ctx, ws.id, name="B", parent_folder_id=None
    )
    moved = await folders_service.rename_or_move_folder(
        session, ctx, b.id, name=None, parent_folder_id=a.id, fields_set={"parent_folder_id"},
    )
    assert moved.parent_folder_id == a.id


async def test_get_folder_checked_rejects_cross_workspace_parent(
    session: AsyncSession, ctx: TenantContext
) -> None:
    ws_a = await tenancy_service.create_workspace(session, ctx, "ws-a")
    ws_b = await tenancy_service.create_workspace(session, ctx, "ws-b")
    folder_in_b = await folders_service.create_folder(
        session, ctx, ws_b.id, name="X", parent_folder_id=None
    )
    with pytest.raises(NotFoundError):
        await folders_service.create_folder(
            session, ctx, ws_a.id, name="Y", parent_folder_id=folder_in_b.id
        )


async def test_delete_folder_cascades_to_subfolders_and_documents(
    session: AsyncSession, ctx: TenantContext
) -> None:
    ws = await tenancy_service.create_workspace(session, ctx, "test-ws")
    parent = await folders_service.create_folder(
        session, ctx, ws.id, name="Legal", parent_folder_id=None
    )
    child = await folders_service.create_folder(
        session, ctx, ws.id, name="Contracts", parent_folder_id=parent.id
    )
    doc_in_parent = await create_from_upload(
        session, ctx, ws.id, filename="a.pdf", mime="application/pdf", data=b"a",
        folder_id=parent.id,
    )
    doc_in_child = await create_from_upload(
        session, ctx, ws.id, filename="b.pdf", mime="application/pdf", data=b"b",
        folder_id=child.id,
    )
    document_ids = await folders_service.delete_folder(session, ctx, parent.id)
    assert len(document_ids) == 2
    assert set(document_ids) == {doc_in_parent.id, doc_in_child.id}

    await session.refresh(doc_in_parent)
    await session.refresh(doc_in_child)
    assert doc_in_parent.status == "deleting"
    assert doc_in_child.status == "deleting"

    remaining_folders = await folders_service.list_folders(session, ctx, ws.id)
    assert remaining_folders == []


async def test_count_subtree_matches_delete_folder_document_count(
    session: AsyncSession, ctx: TenantContext
) -> None:
    """count_subtree is a preview: it must report the exact same counts a
    subsequent delete_folder call would act on, across a multi-level subtree
    (parent -> child -> grandchild, documents scattered at each level, plus
    an unrelated sibling folder that must NOT be counted)."""
    ws = await tenancy_service.create_workspace(session, ctx, "test-ws")
    parent = await folders_service.create_folder(
        session, ctx, ws.id, name="Legal", parent_folder_id=None
    )
    child = await folders_service.create_folder(
        session, ctx, ws.id, name="Contracts", parent_folder_id=parent.id
    )
    grandchild = await folders_service.create_folder(
        session, ctx, ws.id, name="2024", parent_folder_id=child.id
    )
    await folders_service.create_folder(
        session, ctx, ws.id, name="Unrelated", parent_folder_id=None
    )
    await create_from_upload(
        session, ctx, ws.id, filename="a.pdf", mime="application/pdf", data=b"a",
        folder_id=parent.id,
    )
    await create_from_upload(
        session, ctx, ws.id, filename="b.pdf", mime="application/pdf", data=b"b",
        folder_id=child.id,
    )
    await create_from_upload(
        session, ctx, ws.id, filename="c.pdf", mime="application/pdf", data=b"c",
        folder_id=grandchild.id,
    )
    await create_from_upload(
        session, ctx, ws.id, filename="unrelated.pdf", mime="application/pdf", data=b"u",
        folder_id=None,
    )

    document_count, subfolder_count = await folders_service.count_subtree(
        session, ctx, parent.id
    )
    assert document_count == 3
    assert subfolder_count == 2  # child + grandchild, not parent itself

    document_ids = await folders_service.delete_folder(session, ctx, parent.id)
    assert len(document_ids) == document_count


async def test_count_subtree_leaf_folder_with_no_documents(
    session: AsyncSession, ctx: TenantContext
) -> None:
    ws = await tenancy_service.create_workspace(session, ctx, "test-ws")
    folder = await folders_service.create_folder(
        session, ctx, ws.id, name="Empty", parent_folder_id=None
    )
    document_count, subfolder_count = await folders_service.count_subtree(
        session, ctx, folder.id
    )
    assert document_count == 0
    assert subfolder_count == 0


async def test_delete_empty_folder(session: AsyncSession, ctx: TenantContext) -> None:
    ws = await tenancy_service.create_workspace(session, ctx, "test-ws")
    folder = await folders_service.create_folder(
        session, ctx, ws.id, name="Empty", parent_folder_id=None
    )
    document_ids = await folders_service.delete_folder(session, ctx, folder.id)
    assert document_ids == []
    assert await folders_service.list_folders(session, ctx, ws.id) == []


async def test_ensure_path_creates_full_chain(session: AsyncSession, ctx: TenantContext) -> None:
    ws = await tenancy_service.create_workspace(session, ctx, "test-ws")
    deepest = await folders_service.ensure_path(session, ctx, ws.id, "Legal/Contracts/2024")
    assert deepest.name == "2024"
    folders = await folders_service.list_folders(session, ctx, ws.id)
    assert len(folders) == 3
    by_name = {f.name: f for f in folders}
    assert by_name["Contracts"].parent_folder_id == by_name["Legal"].id
    assert by_name["2024"].parent_folder_id == by_name["Contracts"].id


async def test_ensure_path_is_idempotent(session: AsyncSession, ctx: TenantContext) -> None:
    ws = await tenancy_service.create_workspace(session, ctx, "test-ws")
    first = await folders_service.ensure_path(session, ctx, ws.id, "Legal/Contracts")
    second = await folders_service.ensure_path(session, ctx, ws.id, "Legal/Contracts")
    assert first.id == second.id
    folders = await folders_service.list_folders(session, ctx, ws.id)
    assert len(folders) == 2  # not 4 -- no duplicates from the second call


async def test_ensure_path_reuses_existing_prefix(
    session: AsyncSession, ctx: TenantContext
) -> None:
    ws = await tenancy_service.create_workspace(session, ctx, "test-ws")
    await folders_service.ensure_path(session, ctx, ws.id, "Legal/Contracts")
    await folders_service.ensure_path(session, ctx, ws.id, "Legal/Invoices")
    folders = await folders_service.list_folders(session, ctx, ws.id)
    assert len(folders) == 3  # Legal shared, Contracts + Invoices both under it
    legal = next(f for f in folders if f.name == "Legal")
    assert sum(1 for f in folders if f.parent_folder_id == legal.id) == 2


async def test_ensure_path_rejects_empty_path(session: AsyncSession, ctx: TenantContext) -> None:
    ws = await tenancy_service.create_workspace(session, ctx, "test-ws")
    with pytest.raises(ConflictError):
        await folders_service.ensure_path(session, ctx, ws.id, "///")


async def test_ensure_path_concurrent_race_converges_on_one_folder(
    session: AsyncSession, engine: AsyncEngine, ctx: TenantContext
) -> None:
    """Deterministically reproduces the "select misses, insert conflicts"
    race two concurrent uploaders would hit on the same brand-new segment --
    without relying on real thread/asyncio timing, which can't be trusted to
    interleave two coroutines at the exact right point on every run.

    `session`'s next transaction is pinned to REPEATABLE READ and anchored
    (via the throwaway SELECT below) BEFORE a second, independent session
    commits a "Legal" folder. `session` therefore still sees no "Legal" row
    when ensure_path runs its existing-check, tries to INSERT one of its
    own, and collides with the row the other session already committed --
    exactly the concurrent-double-submit scenario the except IntegrityError
    branch exists for. Without that branch (e.g. if it just re-raised),
    this test would fail with an uncaught IntegrityError instead of
    asserting a converged, non-duplicated result."""
    ws = await tenancy_service.create_workspace(session, ctx, "test-ws")
    # Captured up front: ensure_path's own except-branch rollback() (below)
    # expires every ORM object on `session`, this `ws` included, so accessing
    # `ws.id` again afterward would hit the same expired-attribute/
    # MissingGreenlet trap the production fix in folders.py had to route
    # around with its own `ws_id` local.
    ws_id = ws.id

    await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"))
    await session.execute(select(Folder.id).where(Folder.workspace_id == ws_id))

    factory = build_session_factory(engine)
    async with factory() as other_session:
        await folders_service.create_folder(
            other_session, ctx, ws_id, name="Legal", parent_folder_id=None
        )

    # `session`'s repeatable-read snapshot predates the commit above, so its
    # own existing-check misses and its INSERT collides with the now-committed
    # row -- forcing the except IntegrityError -> rollback -> re-read branch.
    deepest = await folders_service.ensure_path(session, ctx, ws_id, "Legal/Contracts")
    assert deepest.name == "Contracts"
    folders = await folders_service.list_folders(session, ctx, ws_id)
    assert len(folders) == 2  # one "Legal" (reused, not duplicated), one "Contracts"
    assert sum(1 for f in folders if f.name == "Legal") == 1
