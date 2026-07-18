import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from raghub.modules.auth.models import User
from raghub.modules.documents.models import Document, IngestJob
from raghub.modules.tenancy.models import Organization, Workspace


async def _seed(session: AsyncSession) -> tuple[Organization, Workspace, User]:
    org = Organization(name="Acme")
    session.add(org)
    await session.flush()
    ws = Workspace(org_id=org.id, name="Fin")
    user = User(org_id=org.id, email="a@a.com", password_hash="x", role="admin")  # noqa: S106
    session.add_all([ws, user])
    await session.flush()
    return org, ws, user


async def test_document_and_job_roundtrip(session: AsyncSession) -> None:
    org, ws, user = await _seed(session)
    doc = Document(org_id=org.id, workspace_id=ws.id, filename="a.pdf",
                   mime="application/pdf", size_bytes=10, content_hash="h1",
                   storage_key=f"{org.id}/{ws.id}/x/a.pdf", created_by=user.id)
    session.add(doc)
    await session.flush()
    session.add(IngestJob(document_id=doc.id, stage="parse"))
    await session.commit()

    found = (await session.execute(select(Document))).scalar_one()
    assert found.status == "queued" and found.page_count is None
    job = (await session.execute(select(IngestJob))).scalar_one()
    assert job.progress == 0.0 and job.finished_at is None


async def test_content_hash_unique_per_workspace(session: AsyncSession) -> None:
    org, ws, user = await _seed(session)
    common = dict(org_id=org.id, workspace_id=ws.id, filename="a.pdf",
                  mime="application/pdf", size_bytes=10, content_hash="dup",
                  storage_key="k", created_by=user.id)
    session.add(Document(**common))
    await session.commit()
    session.add(Document(**common))
    with pytest.raises(IntegrityError):
        await session.commit()
