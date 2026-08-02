from uuid import uuid4

from qdrant_client import models

from ragz.modules.documents.pipeline import Chunk
from ragz.modules.models.models import LOCAL_EMBEDDING_MODEL_ID
from ragz.modules.retrieval.client import EPHEMERAL_COLLECTION, get_qdrant
from ragz.modules.retrieval.embeddings import embed_sparse, get_dense_embedder
from ragz.modules.retrieval.service import (
    _attachment_filter,
    delete_ephemeral_points,
    ensure_ephemeral_collection,
    search_ephemeral_attachments,
    upsert_ephemeral_chunks,
)


def _test_dense_embedder():
    """DOC-10: get_dense_embedder is model-parameterized now; RAGZ_EMBEDDING_BACKEND=hash
    (stack_env) ignores these args and always returns the deterministic hash
    embedder -- the ephemeral store has no per-workspace model choice anyway
    (ensure_ephemeral_collection's own docstring), so LOCAL_EMBEDDING_MODEL_ID
    is the right stand-in identity here regardless."""
    return get_dense_embedder(
        LOCAL_EMBEDDING_MODEL_ID, provider_kind="tei", litellm_model_name="local-embeddings"
    )


def test_attachment_filter_pins_tenant_and_chat_conditions() -> None:
    """Pins the exact shape of the SECOND filter builder (iron rule 1): the
    `must` list has to carry exactly a tenant_id and a chat_id FieldCondition
    — no ACL, no workspace, no current-only clause (unlike `_tenant_filter`,
    this store has no such concepts). Kills the mutant that drops either
    condition while keeping the other."""
    org_id, chat_id = uuid4(), uuid4()
    flt = _attachment_filter(org_id=org_id, chat_id=chat_id)
    assert flt.must is not None
    assert len(flt.must) == 2
    seen = {}
    for cond in flt.must:
        assert isinstance(cond, models.FieldCondition)
        assert isinstance(cond.match, models.MatchValue)
        seen[cond.key] = cond.match.value
    assert seen.get("tenant_id") == str(org_id)
    assert seen.get("chat_id") == str(chat_id)


async def test_ensure_ephemeral_collection_idempotent(qdrant_collection: None) -> None:
    """Calling ensure_ephemeral_collection() twice must not error (Qdrant
    create_payload_index is idempotent) and must leave the collection with
    the tenant_id/chat_id keyword indexes it declares."""
    client = get_qdrant()
    if await client.collection_exists(EPHEMERAL_COLLECTION):
        await client.delete_collection(EPHEMERAL_COLLECTION)
    name = await ensure_ephemeral_collection()
    assert name == EPHEMERAL_COLLECTION
    await ensure_ephemeral_collection()  # second call: must not raise
    info = await client.get_collection(EPHEMERAL_COLLECTION)
    assert "tenant_id" in info.payload_schema
    assert "chat_id" in info.payload_schema


async def test_upsert_and_search_round_trip(qdrant_collection: None) -> None:
    await ensure_ephemeral_collection()
    org_id, chat_id, attachment_id = uuid4(), uuid4(), uuid4()
    embedder = _test_dense_embedder()
    text = "the quarterly report shows a 12% increase"
    chunk = Chunk(text=text, page=1, chunk_index=0)
    dense = (await embedder.embed([text]))[0]
    sparse = embed_sparse([text])[0]
    await upsert_ephemeral_chunks(
        org_id=org_id, chat_id=chat_id, attachment_id=attachment_id,
        chunks=[chunk], dense=[dense], sparse=[sparse],
    )
    query_dense = (await embedder.embed(["quarterly report increase"]))[0]
    query_sparse = embed_sparse(["quarterly report increase"])[0]
    hits = await search_ephemeral_attachments(
        org_id=org_id, chat_id=chat_id, query_dense=query_dense,
        query_sparse=query_sparse, top_k=5,
    )
    assert len(hits) == 1
    assert hits[0].text == text
    assert hits[0].document_id == attachment_id
    assert hits[0].page == 1
    assert hits[0].chunk_index == 0


async def test_upsert_same_attachment_chunk_overwrites_not_duplicates(
    qdrant_collection: None,
) -> None:
    """Deterministic point ids (uuid5 of attachment_id:chunk_index), same
    convention as documents/pipeline.py's _CHUNK_NAMESPACE — a retried
    upsert of the same attachment+chunk_index overwrites in place."""
    await ensure_ephemeral_collection()
    org_id, chat_id, attachment_id = uuid4(), uuid4(), uuid4()
    embedder = _test_dense_embedder()
    chunk = Chunk(text="version one", page=1, chunk_index=0)
    dense = (await embedder.embed(["version one"]))[0]
    sparse = embed_sparse(["version one"])[0]
    await upsert_ephemeral_chunks(
        org_id=org_id, chat_id=chat_id, attachment_id=attachment_id,
        chunks=[chunk], dense=[dense], sparse=[sparse],
    )
    chunk2 = Chunk(text="version two", page=1, chunk_index=0)
    dense2 = (await embedder.embed(["version two"]))[0]
    sparse2 = embed_sparse(["version two"])[0]
    await upsert_ephemeral_chunks(
        org_id=org_id, chat_id=chat_id, attachment_id=attachment_id,
        chunks=[chunk2], dense=[dense2], sparse=[sparse2],
    )
    query_dense = (await embedder.embed(["version"]))[0]
    query_sparse = embed_sparse(["version"])[0]
    hits = await search_ephemeral_attachments(
        org_id=org_id, chat_id=chat_id, query_dense=query_dense,
        query_sparse=query_sparse, top_k=10,
    )
    assert len(hits) == 1
    assert hits[0].text == "version two"


async def test_delete_ephemeral_points_scoped_to_chat_only(qdrant_collection: None) -> None:
    """delete_ephemeral_points(chat_id, attachment_ids) must remove ONLY that
    chat's given attachment(s) — a sibling chat's attachment points (even in
    the same org) must survive. Complements the isolation suite's search-side
    proof with a write-side one."""
    await ensure_ephemeral_collection()
    org_id, chat_to_delete, chat_to_keep = uuid4(), uuid4(), uuid4()
    embedder = _test_dense_embedder()
    attachment_to_delete = uuid4()
    for chat_id, attachment_id, text in [
        (chat_to_delete, attachment_to_delete, "delete me"),
        (chat_to_keep, uuid4(), "keep me"),
    ]:
        chunk = Chunk(text=text, page=1, chunk_index=0)
        dense = (await embedder.embed([text]))[0]
        sparse = embed_sparse([text])[0]
        await upsert_ephemeral_chunks(
            org_id=org_id, chat_id=chat_id, attachment_id=attachment_id,
            chunks=[chunk], dense=[dense], sparse=[sparse],
        )
    await delete_ephemeral_points(chat_to_delete, [attachment_to_delete])
    query_dense = (await embedder.embed(["me"]))[0]
    query_sparse = embed_sparse(["me"])[0]
    hits_deleted = await search_ephemeral_attachments(
        org_id=org_id, chat_id=chat_to_delete, query_dense=query_dense,
        query_sparse=query_sparse, top_k=5,
    )
    assert hits_deleted == []
    hits_kept = await search_ephemeral_attachments(
        org_id=org_id, chat_id=chat_to_keep, query_dense=query_dense,
        query_sparse=query_sparse, top_k=5,
    )
    assert any("keep me" in h.text for h in hits_kept)


async def test_delete_ephemeral_points_scoped_to_attachment_within_same_chat(
    qdrant_collection: None,
) -> None:
    """The specific bug this whole-branch review fix addresses: TWO
    attachments in the SAME chat, only one named in attachment_ids — the
    other (sibling, unlisted) attachment's points must survive untouched."""
    await ensure_ephemeral_collection()
    org_id, chat_id = uuid4(), uuid4()
    stale_attachment_id, fresh_attachment_id = uuid4(), uuid4()
    embedder = _test_dense_embedder()
    for attachment_id, text in [
        (stale_attachment_id, "the stale one"),
        (fresh_attachment_id, "the fresh one"),
    ]:
        chunk = Chunk(text=text, page=1, chunk_index=0)
        dense = (await embedder.embed([text]))[0]
        sparse = embed_sparse([text])[0]
        await upsert_ephemeral_chunks(
            org_id=org_id, chat_id=chat_id, attachment_id=attachment_id,
            chunks=[chunk], dense=[dense], sparse=[sparse],
        )
    await delete_ephemeral_points(chat_id, [stale_attachment_id])
    query_dense = (await embedder.embed(["one"]))[0]
    query_sparse = embed_sparse(["one"])[0]
    hits = await search_ephemeral_attachments(
        org_id=org_id, chat_id=chat_id, query_dense=query_dense,
        query_sparse=query_sparse, top_k=5,
    )
    texts = {h.text for h in hits}
    assert "the fresh one" in texts
    assert "the stale one" not in texts
