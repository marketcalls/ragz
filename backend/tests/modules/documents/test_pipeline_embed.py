import pytest

from ragz.modules.documents.pipeline import embed_batch
from ragz.modules.retrieval.embeddings import HashDenseEmbedder


@pytest.fixture
def hash_dense_embedder() -> HashDenseEmbedder:
    return HashDenseEmbedder(dim=64)


async def test_embed_batch_sparse_texts_override(
    hash_dense_embedder: HashDenseEmbedder,
) -> None:
    dense_a, sparse_a = await embed_batch(["plain text"], hash_dense_embedder)
    dense_b, sparse_b = await embed_batch(
        ["plain text"], hash_dense_embedder, sparse_texts=["plain text ppe safety"]
    )
    assert dense_a == dense_b  # dense untouched by sparse_texts
    assert sparse_a != sparse_b  # sparse reflects the augmented text


async def test_embed_batch_sparse_texts_defaults_to_texts(
    hash_dense_embedder: HashDenseEmbedder,
) -> None:
    dense, sparse = await embed_batch(["x"], hash_dense_embedder)
    dense2, sparse2 = await embed_batch(["x"], hash_dense_embedder, sparse_texts=None)
    assert dense == dense2
    assert sparse == sparse2  # explicit None is identical to omitting it
