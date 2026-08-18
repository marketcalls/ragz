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


async def test_a_redelivered_outbox_event_does_not_run_the_eval_twice(
    session, ctx, ws, qdrant_collection,
) -> None:  # type: ignore[no-untyped-def]
    """Cubic P1: outbox delivery is at-least-once.

    dispatch_pending sends to the broker and only then commits mark_dispatched,
    so a crash in between redelivers the event. Unlike ingest (deterministic
    point ids) and delete (idempotent), a second eval run would add a duplicate
    row AND re-spend the workspace's whole LLM/quota budget. The dispatch_id
    claim must make the redelivery a no-op.
    """
    from uuid import uuid4

    from sqlalchemy import func, select

    from ragz.modules.evals import service
    from ragz.modules.evals.models import EvalRun

    doc = await ingest_text(session, ctx, ws, "policy.txt", "Muster at gate B in an emergency.")
    await service.create_golden_query(
        session, ctx, ws.id, question="Where is the muster point?",
        expected_document_ids=[doc.id],
    )

    # Read ids up front: the duplicate claim below rolls back, which expires
    # every ORM object in this session, and a later attribute access would then
    # lazy-refresh (MissingGreenlet) rather than assert.
    ws_id = ws.id
    event_id = uuid4()
    first = await run_eval(
        session, ws, triggered_by="manual", retriever=retrieve, completer=None,
        dispatch_id=event_id,
    )
    assert first is not None
    assert first.query_count == 1

    # Same event delivered again: skipped, not re-run.
    second = await run_eval(
        session, ws, triggered_by="manual", retriever=retrieve, completer=None,
        dispatch_id=event_id,
    )
    assert second is None

    total = await session.scalar(
        select(func.count()).select_from(EvalRun).where(EvalRun.workspace_id == ws_id)
    )
    assert total == 1, "the redelivery must not leave a second run in the history"


async def test_distinct_events_and_unkeyed_runs_are_unaffected(
    session, ctx, ws, qdrant_collection,
) -> None:  # type: ignore[no-untyped-def]
    """The claim must not over-deduplicate: two genuine triggers are two runs,
    and callers with no outbox event behind them (the nightly fan-out) pass no
    dispatch_id and keep working exactly as before -- NULLs do not collide."""
    from uuid import uuid4

    from sqlalchemy import func, select

    from ragz.modules.evals.models import EvalRun

    assert await run_eval(
        session, ws, triggered_by="manual", retriever=retrieve, completer=None,
        dispatch_id=uuid4(),
    ) is not None
    assert await run_eval(
        session, ws, triggered_by="manual", retriever=retrieve, completer=None,
        dispatch_id=uuid4(),
    ) is not None
    # No key at all, twice: the pre-existing contract.
    assert await run_eval(
        session, ws, triggered_by="nightly", retriever=retrieve, completer=None,
    ) is not None
    assert await run_eval(
        session, ws, triggered_by="nightly", retriever=retrieve, completer=None,
    ) is not None

    total = await session.scalar(
        select(func.count()).select_from(EvalRun).where(EvalRun.workspace_id == ws.id)
    )
    assert total == 4
