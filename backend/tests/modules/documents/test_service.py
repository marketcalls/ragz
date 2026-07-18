import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from raghub.core.config import get_settings
from raghub.core.errors import ConflictError, NotFoundError, WorkspaceAccessDenied
from raghub.core.storage import build_storage
from raghub.modules.audit.models import AuditEvent
from raghub.modules.documents.service import (
    create_from_upload,
    get_document_checked,
    list_documents,
)
from tests.modules.retrieval.test_retrieve import seed_workspace


async def test_upload_stores_row_object_and_audit(
    session: AsyncSession, stack_env: None
) -> None:
    ctx, ws = await seed_workspace(session, "up1")
    doc = await create_from_upload(session, ctx, ws.id, filename="a.txt",
                                   mime="text/plain", data=b"hello world")
    assert doc.status == "queued" and doc.size_bytes == 11
    assert doc.storage_key == f"{ctx.org_id}/{ws.id}/{doc.id}/a.txt"
    storage = build_storage(get_settings())
    assert await storage.get(doc.storage_key) == b"hello world"
    actions = [e.action for e in (await session.execute(select(AuditEvent))).scalars()]
    assert "document.uploaded" in actions


async def test_duplicate_content_conflicts(session: AsyncSession, stack_env: None) -> None:
    ctx, ws = await seed_workspace(session, "up2")
    await create_from_upload(session, ctx, ws.id, filename="a.txt",
                             mime="text/plain", data=b"same bytes")
    with pytest.raises(ConflictError):
        await create_from_upload(session, ctx, ws.id, filename="b.txt",
                                 mime="text/plain", data=b"same bytes")


async def test_non_member_cannot_upload_or_list(
    session: AsyncSession, stack_env: None
) -> None:
    ctx, ws = await seed_workspace(session, "up3", member=False)
    with pytest.raises(WorkspaceAccessDenied):
        await create_from_upload(session, ctx, ws.id, filename="a.txt",
                                 mime="text/plain", data=b"x")
    with pytest.raises(WorkspaceAccessDenied):
        await list_documents(session, ctx, ws.id)


async def test_list_and_get_checked(session: AsyncSession, stack_env: None) -> None:
    ctx, ws = await seed_workspace(session, "up4")
    doc = await create_from_upload(session, ctx, ws.id, filename="a.txt",
                                   mime="text/plain", data=b"abc")
    docs = await list_documents(session, ctx, ws.id)
    assert [d.id for d in docs] == [doc.id]
    assert (await get_document_checked(session, ctx, doc.id)).id == doc.id

    other_ctx, _ = await seed_workspace(session, "up5")
    with pytest.raises(NotFoundError):  # cross-org: existence never leaks
        await get_document_checked(session, other_ctx, doc.id)
