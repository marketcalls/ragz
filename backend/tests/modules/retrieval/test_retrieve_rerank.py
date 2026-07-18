import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from raghub.core.config import get_settings
from raghub.modules.retrieval.rerank import get_reranker
from raghub.modules.retrieval.service import retrieve
from tests.modules.retrieval.test_retrieve import seed_workspace, upsert_texts


async def test_workspace_top_k_applies_when_caller_passes_none(
    session: AsyncSession, qdrant_collection: None
) -> None:
    ctx, ws = await seed_workspace(session, "topkOrg", top_k=1)
    await upsert_texts(ctx, ws, ["alpha report one", "alpha report two", "alpha report three"])
    result = await retrieve(session, ctx, ws.id, "alpha report")
    assert len(result.chunks) == 1
    explicit = await retrieve(session, ctx, ws.id, "alpha report", top_k=3)
    assert len(explicit.chunks) == 3


async def test_rerank_orders_and_scores_by_reranker(
    session: AsyncSession, qdrant_collection: None
) -> None:
    # Lexical backend (stack_env): score == fraction of query tokens in the text.
    ctx, ws = await seed_workspace(session, "rerankOrg", rerank_enabled=True, top_k=2)
    await upsert_texts(ctx, ws, [
        "quarterly budget summary and numbers",
        "the launch checklist steps",
        "unrelated meeting notes",
    ])
    result = await retrieve(session, ctx, ws.id, "launch checklist")
    assert result.chunks[0].text == "the launch checklist steps"
    # RRF fusion scores are rank-based (~1/rank sums); an exact 1.0 proves the
    # score came from the reranker, not fusion.
    assert result.chunks[0].score == 1.0
    assert result.no_answer is False  # 1.0 >= min_score 0.0


async def test_rerank_min_score_reads_in_reranker_space(
    session: AsyncSession, qdrant_collection: None
) -> None:
    ctx, ws = await seed_workspace(
        session, "rerankThreshOrg", rerank_enabled=True, min_score=0.9
    )
    await upsert_texts(ctx, ws, ["the launch steps only"])
    # 1 of 2 query tokens present -> lexical score 0.5 < 0.9 -> no_answer,
    # nearest chunks still returned (CHAT-9 behavior preserved).
    result = await retrieve(session, ctx, ws.id, "launch checklist")
    assert result.no_answer is True
    assert len(result.chunks) == 1 and result.chunks[0].score == 0.5


async def test_reranker_down_degrades_to_fusion_order(
    session: AsyncSession, qdrant_collection: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """NFR graceful degradation: TEI backend pointed at a dead port must not
    fail the request — fusion order + dense-cosine threshold come back."""
    monkeypatch.setenv("RAGHUB_RERANK_BACKEND", "tei")
    monkeypatch.setenv("RAGHUB_RERANK_URL", "http://127.0.0.1:9")  # nothing listens
    get_settings.cache_clear()
    get_reranker.cache_clear()
    try:
        ctx, ws = await seed_workspace(session, "rerankDownOrg", rerank_enabled=True, top_k=1)
        # A second doc that out-ranks the target on the sparse channel while
        # losing on the dense channel: with only one candidate, Qdrant's RRF
        # fusion trivially normalizes the sole hit to a score of 1.0 (verified
        # against the real container), which would make the "not a lexical
        # 1.0" assertion below meaningless. Crossing the per-channel ranks
        # keeps the target's fused score below 1.0 so it actually proves the
        # fallback path returns a genuine fusion score, not a reranker score.
        await upsert_texts(ctx, ws, [
            "alpha launch checklist",
            "launch checklist launch checklist extra words padding here now",
        ])
        result = await retrieve(session, ctx, ws.id, "launch checklist")
        assert len(result.chunks) == 1
        assert result.chunks[0].score != 1.0  # fusion/RRF score, not a lexical 1.0
    finally:
        get_settings.cache_clear()
        get_reranker.cache_clear()
