import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from raghub.core.errors import ConflictError, NotFoundError
from raghub.modules.auth.models import User
from raghub.modules.documents import folders as folders_service
from raghub.modules.tenancy import service as tenancy_service
from raghub.modules.tenancy.context import TenantContext
from raghub.modules.tenancy.models import Organization


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
