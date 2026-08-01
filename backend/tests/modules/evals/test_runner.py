"""Eval runner (Phase 3 §6): hit-rate/citation-precision always compute;
faithfulness only with a utility model + a synthesizable answer."""

from ragz.modules.evals.runner import run_eval
from ragz.modules.models.models import Model
from ragz.modules.retrieval.service import retrieve
from tests.conftest import FakeCompleter
from tests.isolation.conftest import ingest_text


async def test_hit_rate_and_precision_without_utility_model(
    session, ctx, ws, qdrant_collection,
) -> None:  # type: ignore[no-untyped-def]
    doc = await ingest_text(session, ctx, ws, "policy.txt", "Muster at gate B in an emergency.")
    from ragz.modules.evals import service
    await service.create_golden_query(
        session, ctx, ws.id, question="Where is the muster point?",
        expected_document_ids=[doc.id],
    )
    run = await run_eval(session, ws, triggered_by="manual", retriever=retrieve, completer=None)
    assert run.query_count == 1
    assert run.hit_rate == 1.0
    assert run.citation_precision == 1.0
    assert run.avg_faithfulness is None  # no utility model -> unavailable, not zero


async def test_off_corpus_query_counts_as_hit_when_nothing_retrieved(
    session, ctx, ws, qdrant_collection,
) -> None:  # type: ignore[no-untyped-def]
    await ingest_text(session, ctx, ws, "unrelated.txt", "Completely unrelated content.")
    # The evals ctx/ws fixture seeds min_score=0.0 (never declines) -- an
    # off-corpus probe needs a real confidence floor to be meaningful, so the
    # retriever's no_answer verdict has something to trigger on. With the
    # hash embedder, two token-disjoint texts score cosine ~0.0, which is
    # only "not good enough" once min_score is above zero.
    ws.min_score = 0.3
    await session.commit()
    from ragz.modules.evals import service
    await service.create_golden_query(
        session, ctx, ws.id, question="What is the capital of a fictional planet?",
        expected_document_ids=[],
    )
    run = await run_eval(session, ws, triggered_by="manual", retriever=retrieve, completer=None)
    assert run.hit_rate == 1.0  # correctly retrieved nothing for an off-corpus probe


async def test_faithfulness_computed_with_utility_model(
    session, ctx, ws, qdrant_collection, utility_model,
) -> None:  # type: ignore[no-untyped-def]
    from ragz.modules.chat.llm import LLMCompletion, LLMUsage
    doc = await ingest_text(session, ctx, ws, "policy.txt", "Muster at gate B in an emergency.")
    # A default (synthesis) model is required in addition to the utility
    # (judge) model designated by the `utility_model` fixture -- faithfulness
    # is unavailable unless BOTH are present (pinned in the brief).
    default_model = Model(
        litellm_model_name="chat-model", display_name="Chat", provider_kind="ollama",
        enabled=True,
    )
    session.add(default_model)
    await session.flush()
    ws.default_model_id = default_model.id
    await session.commit()
    from ragz.modules.evals import service
    await service.create_golden_query(
        session, ctx, ws.id, question="Where is the muster point?",
        expected_document_ids=[doc.id],
    )
    completer = FakeCompleter([
        LLMCompletion(text="Gate B.", tool_calls=[], usage=LLMUsage(20, 5)),         # synth
        LLMCompletion(text='{"faithfulness": 5}', tool_calls=[], usage=LLMUsage(15, 3)),  # judge
    ])
    run = await run_eval(
        session, ws, triggered_by="manual", retriever=retrieve, completer=completer,
    )
    assert run.avg_faithfulness == 5.0


async def test_faithfulness_unavailable_without_default_model(
    session, ctx, ws, qdrant_collection, utility_model,
) -> None:  # type: ignore[no-untyped-def]
    """A utility model alone isn't enough -- no workspace default_model_id
    means there's nothing to synthesize an answer with, so faithfulness stays
    None (not zero), even though a completer and a utility model both exist."""
    doc = await ingest_text(session, ctx, ws, "policy.txt", "Muster at gate B in an emergency.")
    from ragz.modules.evals import service
    await service.create_golden_query(
        session, ctx, ws.id, question="Where is the muster point?",
        expected_document_ids=[doc.id],
    )
    completer = FakeCompleter([])
    run = await run_eval(
        session, ws, triggered_by="manual", retriever=retrieve, completer=completer,
    )
    assert run.avg_faithfulness is None
    assert run.hit_rate == 1.0
    assert run.citation_precision == 1.0
