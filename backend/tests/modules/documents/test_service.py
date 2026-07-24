import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from raghub.core.config import get_settings
from raghub.core.db import naive_utc
from raghub.core.errors import ConflictError, NotFoundError, WorkspaceAccessDenied
from raghub.core.storage import build_storage
from raghub.modules.audit.models import AuditEvent
from raghub.modules.auth.models import User
from raghub.modules.documents import folders as folders_service
from raghub.modules.documents import service
from raghub.modules.documents.service import (
    create_from_upload,
    get_document_checked,
    list_documents,
    move_document,
)
from raghub.modules.tenancy import service as tenancy_service
from raghub.modules.tenancy.context import TenantContext
from raghub.modules.tenancy.models import Organization
from raghub.modules.tenancy.reembed_models import ReembedJob
from tests.modules.retrieval.test_retrieve import seed_workspace


@pytest.fixture
async def ctx(session: AsyncSession) -> TenantContext:
    """Real Organization + User rows (Workspace.org_id and Folder.created_by
    FK to them) -- role="admin" so get_workspace_checked/get_folder_checked
    never need explicit workspace membership, mirroring
    tests/modules/documents/test_folders.py's identically-shaped fixture."""
    org = Organization(name="doc-svc-org")
    session.add(org)
    await session.flush()
    user = User(
        org_id=org.id, email="doc-svc@test.com", password_hash="x",  # noqa: S106
        role="admin",
    )
    session.add(user)
    await session.flush()
    return TenantContext(
        user_id=user.id, org_id=org.id, role="admin",
        workspace_ids=frozenset(), group_ids=frozenset(),
    )


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


async def test_reupload_same_filename_creates_next_version(
    session: AsyncSession, stack_env: None
) -> None:
    ctx, ws = await seed_workspace(session, "up6")
    v1 = await create_from_upload(session, ctx, ws.id, filename="policy.pdf",
                                  mime="application/pdf", data=b"v1 body")
    assert (v1.version, v1.lineage_id, v1.supersedes_document_id) == (1, v1.id, None)
    assert v1.is_current is False and v1.approved is False

    v2 = await create_from_upload(session, ctx, ws.id, filename="policy.pdf",
                                  mime="application/pdf", data=b"v2 body")
    assert v2.version == 2
    assert v2.lineage_id == v1.id
    assert v2.supersedes_document_id == v1.id


async def test_different_filename_starts_new_lineage(
    session: AsyncSession, stack_env: None
) -> None:
    ctx, ws = await seed_workspace(session, "up7")
    a = await create_from_upload(session, ctx, ws.id, filename="a.pdf",
                                 mime="application/pdf", data=b"aaa")
    b = await create_from_upload(session, ctx, ws.id, filename="b.pdf",
                                 mime="application/pdf", data=b"bbb")
    assert a.lineage_id != b.lineage_id and b.version == 1


async def test_identical_content_still_conflicts(
    session: AsyncSession, stack_env: None
) -> None:
    ctx, ws = await seed_workspace(session, "up8")
    await create_from_upload(session, ctx, ws.id, filename="c.pdf",
                             mime="application/pdf", data=b"same")
    with pytest.raises(ConflictError):
        await create_from_upload(session, ctx, ws.id, filename="c.pdf",
                                 mime="application/pdf", data=b"same")


async def test_upload_rejected_while_reembed_job_running(
    session: AsyncSession, stack_env: None
) -> None:
    """Final-review fix (DOC-10): a re-embed job snapshots the workspace's
    documents ONCE at job start and, on completion, does a workspace-WIDE
    delete of the OLD collection. A document uploaded mid-job resolves the
    OLD collection (the workspace's embedding_model_id hasn't flipped yet)
    but was never in the snapshot, so it's never re-embedded into the NEW
    collection -- the job's closing workspace-wide delete then wipes its
    vectors with no error anywhere. Rejecting the upload outright while a
    job is actively running (started_at set, finished_at NULL) closes that
    hole, mirroring set_embedding_model's existing lock-after-first-index
    409 posture."""
    ctx, ws = await seed_workspace(session, "reembed-guard1")
    job = ReembedJob(
        workspace_id=ws.id,
        old_embedding_model_id=ws.embedding_model_id,
        new_embedding_model_id=ws.embedding_model_id,
        documents_total=1,
        started_at=naive_utc(),
    )
    session.add(job)
    await session.commit()

    with pytest.raises(ConflictError, match="re-embed job is in progress"):
        await create_from_upload(session, ctx, ws.id, filename="a.txt",
                                 mime="text/plain", data=b"during reembed")


async def test_upload_succeeds_after_reembed_job_finishes(
    session: AsyncSession, stack_env: None
) -> None:
    """Once finished_at is stamped the guard clears -- and the common case
    (no ReembedJob row for the workspace at all) is exercised by every other
    upload test in this file, since none of them create one."""
    ctx, ws = await seed_workspace(session, "reembed-guard2")
    job = ReembedJob(
        workspace_id=ws.id,
        old_embedding_model_id=ws.embedding_model_id,
        new_embedding_model_id=ws.embedding_model_id,
        documents_total=1,
        documents_done=1,
        started_at=naive_utc(),
        finished_at=naive_utc(),
    )
    session.add(job)
    await session.commit()

    doc = await create_from_upload(session, ctx, ws.id, filename="a.txt",
                                   mime="text/plain", data=b"after reembed")
    assert doc.status == "queued"


async def test_has_indexed_documents(session: AsyncSession, stack_env: None) -> None:
    """Phase 3 escalation gate (Task 10): only a workspace with an actually
    INDEXED document reports true — queued documents don't count."""
    ctx, ws = await seed_workspace(session, "up9")
    assert not await service.has_indexed_documents(session, ctx, ws.id)
    doc = await service.create_from_upload(
        session, ctx, ws.id, filename="a.pdf", mime="application/pdf", data=b"x"
    )
    assert not await service.has_indexed_documents(session, ctx, ws.id)  # not indexed yet
    doc.status = "indexed"
    await session.commit()
    assert await service.has_indexed_documents(session, ctx, ws.id)


async def test_same_filename_in_different_folders_is_independent_lineage(
    session: AsyncSession, ctx: TenantContext
) -> None:
    ws = await tenancy_service.create_workspace(session, ctx, "test-ws")
    folder_a = await folders_service.create_folder(
        session, ctx, ws.id, name="A", parent_folder_id=None
    )
    folder_b = await folders_service.create_folder(
        session, ctx, ws.id, name="B", parent_folder_id=None
    )
    doc_a = await create_from_upload(
        session, ctx, ws.id, filename="report.pdf", mime="application/pdf",
        data=b"content-a", folder_id=folder_a.id,
    )
    doc_b = await create_from_upload(
        session, ctx, ws.id, filename="report.pdf", mime="application/pdf",
        data=b"content-b", folder_id=folder_b.id,
    )
    assert doc_a.lineage_id != doc_b.lineage_id
    assert doc_a.version == 1
    assert doc_b.version == 1


async def test_same_filename_same_folder_still_versions(
    session: AsyncSession, ctx: TenantContext
) -> None:
    ws = await tenancy_service.create_workspace(session, ctx, "test-ws")
    folder = await folders_service.create_folder(
        session, ctx, ws.id, name="A", parent_folder_id=None
    )
    v1 = await create_from_upload(
        session, ctx, ws.id, filename="report.pdf", mime="application/pdf",
        data=b"v1", folder_id=folder.id,
    )
    v2 = await create_from_upload(
        session, ctx, ws.id, filename="report.pdf", mime="application/pdf",
        data=b"v2", folder_id=folder.id,
    )
    assert v2.lineage_id == v1.lineage_id
    assert v2.version == 2
    assert v2.supersedes_document_id == v1.id


async def test_move_document_preserves_lineage(
    session: AsyncSession, ctx: TenantContext
) -> None:
    ws = await tenancy_service.create_workspace(session, ctx, "test-ws")
    folder = await folders_service.create_folder(
        session, ctx, ws.id, name="A", parent_folder_id=None
    )
    doc = await create_from_upload(
        session, ctx, ws.id, filename="report.pdf", mime="application/pdf", data=b"x",
    )
    original_lineage, original_version = doc.lineage_id, doc.version
    moved = await move_document(session, ctx, doc.id, folder.id)
    assert moved.folder_id == folder.id
    assert moved.lineage_id == original_lineage
    assert moved.version == original_version
