import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from raghub.modules.documents.ingest import run_chunk, run_embed_upsert, run_parse
from raghub.modules.documents.models import Document
from raghub.modules.documents.service import create_from_upload
from raghub.modules.tenancy.context import TenantContext
from raghub.modules.tenancy.models import Workspace
from tests.modules.retrieval.test_retrieve import seed_workspace


async def ingest_text(
    session: AsyncSession, ctx: TenantContext, ws: Workspace, filename: str, text: str
) -> Document:
    """Seed via the REAL pipeline: upload service -> parse -> chunk -> embed+upsert."""
    doc = await create_from_upload(session, ctx, ws.id, filename=filename,
                                   mime="text/plain", data=text.encode())
    await run_parse(doc.id)
    await run_chunk(doc.id)
    await run_embed_upsert(doc.id)
    await session.refresh(doc)
    assert doc.status == "indexed"
    return doc


@pytest.fixture
async def two_orgs(
    session: AsyncSession, qdrant_collection: None
) -> dict[str, tuple[TenantContext, Workspace, Document]]:
    ctx_a, ws_a = await seed_workspace(session, "isoA")
    ctx_b, ws_b = await seed_workspace(session, "isoB")
    doc_a = await ingest_text(session, ctx_a, ws_a, "a.txt",
                              "org alpha secret: the vault code is 7431")
    doc_b = await ingest_text(session, ctx_b, ws_b, "b.txt",
                              "org bravo secret: the vault code is 9962")
    return {"a": (ctx_a, ws_a, doc_a), "b": (ctx_b, ws_b, doc_b)}
