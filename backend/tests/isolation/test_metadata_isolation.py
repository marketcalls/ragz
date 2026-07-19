"""Adversarial metadata pre-filtering test (DOC-6, Task 10).

Metadata clauses narrow the ONE Qdrant filter builder (_tenant_filter) — they
must never be bypassable via a query-text lure. If this test fails, treat it
as a security incident, not a flake: an answer must never cite a document
excluded by the caller's own metadata filter.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from raghub.modules.documents.metadata import build_clauses, list_fields, set_document_metadata
from raghub.modules.retrieval.service import retrieve
from tests.isolation.conftest import ingest_text
from tests.modules.retrieval.test_retrieve import seed_workspace


async def test_metadata_filter_excludes_document_despite_exact_text_lure(
    session: AsyncSession, qdrant_collection: None
) -> None:
    """One workspace, two documents differing only by department. Querying
    with the OPS document's exact text as the lure, but filtered to
    department=HSE, must never surface the OPS document. Non-vacuous: the
    HSE document (also a real match for the query) must still come back."""
    ctx, ws = await seed_workspace(session, "metaIso1", min_score=0.0)
    await list_fields(session, ctx, ws.id)  # seed presets incl department

    doc_hse = await ingest_text(
        session, ctx, ws, "hse.txt", "fire extinguishers must be inspected monthly"
    )
    doc_ops = await ingest_text(
        session, ctx, ws, "ops.txt",
        "fire extinguishers must be inspected monthly ops variant",
    )
    await set_document_metadata(session, ctx, doc_hse.id, {"department": "HSE"})
    await set_document_metadata(session, ctx, doc_ops.id, {"department": "OPS"})

    clauses = await build_clauses(session, ctx, ws.id, {"department": "HSE"})
    result = await retrieve(
        session, ctx, ws.id,
        "fire extinguishers must be inspected monthly ops variant",  # OPS's exact text, as lure
        top_k=10, metadata_clauses=clauses,
    )
    returned = {c.document_id for c in result.chunks}
    assert doc_ops.id not in returned
    assert doc_hse.id in returned
