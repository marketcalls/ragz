import asyncio
from datetime import datetime
from uuid import uuid4, uuid5

from sqlalchemy.ext.asyncio import AsyncSession

from raghub.modules.documents.pipeline import (
    _CHUNK_NAMESPACE,
    _HQ_NAMESPACE,
    Chunk,
    upsert_hq_points,
    upsert_points,
)
from raghub.modules.models.models import LOCAL_EMBEDDING_MODEL_ID
from raghub.modules.retrieval.client import COLLECTION, get_qdrant
from raghub.modules.retrieval.embeddings import embed_sparse, get_dense_embedder
from tests.modules.retrieval.test_retrieve import seed_workspace


def _test_dense_embedder():
    """DOC-10: get_dense_embedder is model-parameterized now; RAGHUB_EMBEDDING_BACKEND=hash
    (set by the stack_env fixture) ignores these args and always returns the
    deterministic hash embedder, so the exact values only need to match the
    seeded LOCAL_EMBEDDING_MODEL_ID row for readability, not correctness."""
    return get_dense_embedder(
        LOCAL_EMBEDDING_MODEL_ID, provider_kind="tei", litellm_model_name="local-embeddings"
    )


async def test_upsert_points_summary_defaults_none(
    session: AsyncSession, qdrant_collection: None
) -> None:
    ctx, ws = await seed_workspace(session, "upsertSummaryDefaultOrg")
    doc_id = uuid4()
    chunk = Chunk(text="body", page=1, chunk_index=0)
    dense = await _test_dense_embedder().embed([chunk.text])
    sparse = await asyncio.to_thread(embed_sparse, [chunk.text])
    await upsert_points(
        org_id=ctx.org_id, workspace_id=ws.id, document_id=doc_id,
        mime="text/plain", created_at=datetime.now(), acl_group_ids=[],
        chunks=[chunk], dense=dense, sparse=sparse,
        version=1, meta=None, collection_name=COLLECTION,
    )
    point_id = str(uuid5(_CHUNK_NAMESPACE, f"{doc_id}:0"))
    point = (await get_qdrant().retrieve(COLLECTION, ids=[point_id]))[0]
    assert point.payload is not None
    assert point.payload["summary"] is None


async def test_upsert_points_summary_set_when_provided(
    session: AsyncSession, qdrant_collection: None
) -> None:
    ctx, ws = await seed_workspace(session, "upsertSummarySetOrg")
    doc_id = uuid4()
    chunk = Chunk(text="body", page=1, chunk_index=0)
    dense = await _test_dense_embedder().embed([chunk.text])
    sparse = await asyncio.to_thread(embed_sparse, [chunk.text])
    await upsert_points(
        org_id=ctx.org_id, workspace_id=ws.id, document_id=doc_id,
        mime="text/plain", created_at=datetime.now(), acl_group_ids=[],
        chunks=[chunk], dense=dense, sparse=sparse,
        version=1, meta=None, summaries=["a short summary"], collection_name=COLLECTION,
    )
    point_id = str(uuid5(_CHUNK_NAMESPACE, f"{doc_id}:0"))
    point = (await get_qdrant().retrieve(COLLECTION, ids=[point_id]))[0]
    assert point.payload is not None
    assert point.payload["summary"] == "a short summary"


async def test_upsert_hq_points_mirror_parent_payload_with_kind_hq(
    session: AsyncSession, qdrant_collection: None
) -> None:
    ctx, ws = await seed_workspace(session, "hqMirrorOrg")
    doc_id = uuid4()
    chunk = Chunk(text="wear PPE in zone 2", page=3, chunk_index=7, section="Safety > PPE")
    question = "What PPE is required in zone 2?"
    dense = await _test_dense_embedder().embed([question])
    sparse = await asyncio.to_thread(embed_sparse, [question])
    await upsert_hq_points(
        org_id=ctx.org_id, workspace_id=ws.id, document_id=doc_id,
        mime="text/plain", created_at=datetime.now(), acl_group_ids=["g1"],
        version=2, meta={"doc_type": "policy"}, is_current=True,
        parent_chunks=[chunk], parent_summaries=["PPE required in zone 2"],
        hq_texts=[[question]],
        hq_dense=[dense], hq_sparse=[sparse], collection_name=COLLECTION,
    )
    hq_id = str(uuid5(_HQ_NAMESPACE, f"{doc_id}:7:hq:0"))
    point = (await get_qdrant().retrieve(COLLECTION, ids=[hq_id]))[0]
    payload = point.payload
    assert payload is not None
    assert payload["kind"] == "hq"
    assert payload["text"] == "wear PPE in zone 2"  # parent's text, not the question
    assert payload["page"] == 3 and payload["chunk_index"] == 7
    assert payload["section"] == "Safety > PPE"
    assert payload["version"] == 2 and payload["is_current"] is True
    assert payload["acl_groups"] == ["g1"]
    assert payload["meta"] == {"doc_type": "policy"}
    assert payload["summary"] == "PPE required in zone 2"
    assert payload["tenant_id"] == str(ctx.org_id)
    assert payload["workspace_id"] == str(ws.id)
    assert payload["document_id"] == str(doc_id)
    assert payload["doc_type"] == "text/plain"


async def test_upsert_hq_points_payload_matches_upsert_points_payload_exactly(
    session: AsyncSession, qdrant_collection: None
) -> None:
    """The single most important correctness property (Plan K Task 4): for the
    SAME parent chunk, upsert_hq_points's payload must be byte-identical to what
    upsert_points would write, except for the added `kind` key."""
    ctx, ws = await seed_workspace(session, "hqParityOrg")
    doc_id = uuid4()
    chunk = Chunk(text="body text", page=2, chunk_index=1, section="A > B")
    dense = await _test_dense_embedder().embed([chunk.text])
    sparse = await asyncio.to_thread(embed_sparse, [chunk.text])
    created_at = datetime.now()

    await upsert_points(
        org_id=ctx.org_id, workspace_id=ws.id, document_id=doc_id,
        mime="text/plain", created_at=created_at, acl_group_ids=["g1", "g2"],
        chunks=[chunk], dense=dense, sparse=sparse,
        version=3, meta={"k": "v"}, is_current=True, summaries=["sum"],
        collection_name=COLLECTION,
    )
    parent_id = str(uuid5(_CHUNK_NAMESPACE, f"{doc_id}:1"))
    parent_payload = (await get_qdrant().retrieve(COLLECTION, ids=[parent_id]))[0].payload
    assert parent_payload is not None

    question = "What is body text?"
    q_dense = await _test_dense_embedder().embed([question])
    q_sparse = await asyncio.to_thread(embed_sparse, [question])
    await upsert_hq_points(
        org_id=ctx.org_id, workspace_id=ws.id, document_id=doc_id,
        mime="text/plain", created_at=created_at, acl_group_ids=["g1", "g2"],
        version=3, meta={"k": "v"}, is_current=True,
        parent_chunks=[chunk], parent_summaries=["sum"],
        hq_texts=[[question]], hq_dense=[q_dense], hq_sparse=[q_sparse],
        collection_name=COLLECTION,
    )
    hq_id = str(uuid5(_HQ_NAMESPACE, f"{doc_id}:1:hq:0"))
    hq_payload = (await get_qdrant().retrieve(COLLECTION, ids=[hq_id]))[0].payload
    assert hq_payload is not None

    expected_hq_payload = {**parent_payload, "kind": "hq"}
    assert hq_payload == expected_hq_payload


def test_upsert_hq_points_id_never_collides_with_parent() -> None:
    doc_id = uuid4()
    parent_id = uuid5(_CHUNK_NAMESPACE, f"{doc_id}:0")
    hq_id = uuid5(_HQ_NAMESPACE, f"{doc_id}:0:hq:0")
    assert parent_id != hq_id
