"""Adversarial isolation tests for the ephemeral per-chat attachments store
(iron rule 1's SECOND sanctioned filter, `_attachment_filter`).

Ephemeral attachments live in their own Qdrant collection with their own
access model: "visible to this one chat only, to whoever can already see
that chat" — not a workspace-membership or ACL-group question. These tests
exist to catch any regression in `_attachment_filter` exactly as
test_tenant_isolation.py does for `_tenant_filter`. If any test here fails,
treat it as a security incident, not a flake.
"""

from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession


async def test_ephemeral_search_never_crosses_chats(
    session: AsyncSession, qdrant_collection: None
) -> None:
    from raghub.modules.documents.pipeline import Chunk
    from raghub.modules.models import service as models_service
    from raghub.modules.models.models import LOCAL_EMBEDDING_MODEL_ID
    from raghub.modules.retrieval.embeddings import embed_sparse, get_dense_embedder
    from raghub.modules.retrieval.service import (
        ensure_ephemeral_collection,
        search_ephemeral_attachments,
        upsert_ephemeral_chunks,
    )

    await ensure_ephemeral_collection()
    org_id, chat_a, chat_b = uuid4(), uuid4(), uuid4()
    # DOC-10: the ephemeral store has no per-workspace embedding choice --
    # always the seeded local model (mirrors chat/service.py's route_attachment).
    ephemeral_model = await models_service.get_model(session, LOCAL_EMBEDDING_MODEL_ID)
    embedder = get_dense_embedder(
        ephemeral_model.id, provider_kind=ephemeral_model.provider_kind,
        litellm_model_name=ephemeral_model.litellm_model_name,
    )

    for chat_id, text in [(chat_a, "the vault code is 7431"), (chat_b, "the vault code is 9962")]:
        chunk = Chunk(text=text, page=1, chunk_index=0)
        dense = (await embedder.embed([text]))[0]
        sparse = embed_sparse([text])[0]
        await upsert_ephemeral_chunks(
            org_id=org_id, chat_id=chat_id, attachment_id=uuid4(),
            chunks=[chunk], dense=[dense], sparse=[sparse],
        )

    query_dense = (await embedder.embed(["what is the vault code"]))[0]
    query_sparse = embed_sparse(["what is the vault code"])[0]
    hits_a = await search_ephemeral_attachments(
        org_id=org_id, chat_id=chat_a, query_dense=query_dense,
        query_sparse=query_sparse, top_k=5,
    )
    assert any("7431" in h.text for h in hits_a)
    assert not any("9962" in h.text for h in hits_a)


async def test_ephemeral_search_never_crosses_orgs(
    session: AsyncSession, qdrant_collection: None
) -> None:
    from raghub.modules.documents.pipeline import Chunk
    from raghub.modules.models import service as models_service
    from raghub.modules.models.models import LOCAL_EMBEDDING_MODEL_ID
    from raghub.modules.retrieval.embeddings import embed_sparse, get_dense_embedder
    from raghub.modules.retrieval.service import (
        ensure_ephemeral_collection,
        search_ephemeral_attachments,
        upsert_ephemeral_chunks,
    )

    await ensure_ephemeral_collection()
    chat_id = uuid4()
    org_a, org_b = uuid4(), uuid4()
    ephemeral_model = await models_service.get_model(session, LOCAL_EMBEDDING_MODEL_ID)
    embedder = get_dense_embedder(
        ephemeral_model.id, provider_kind=ephemeral_model.provider_kind,
        litellm_model_name=ephemeral_model.litellm_model_name,
    )
    chunk = Chunk(text="org secret alpha", page=1, chunk_index=0)
    dense = (await embedder.embed(["org secret alpha"]))[0]
    sparse = embed_sparse(["org secret alpha"])[0]
    await upsert_ephemeral_chunks(
        org_id=org_a, chat_id=chat_id, attachment_id=uuid4(),
        chunks=[chunk], dense=[dense], sparse=[sparse],
    )
    query_dense = (await embedder.embed(["secret"]))[0]
    query_sparse = embed_sparse(["secret"])[0]
    hits = await search_ephemeral_attachments(
        org_id=org_b, chat_id=chat_id, query_dense=query_dense,
        query_sparse=query_sparse, top_k=5,
    )
    assert hits == []
