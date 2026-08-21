"""Turn outbox rows into Celery messages.

Lives in `worker/`, not in the outbox module, on purpose: this is the ONLY place
that knows task names and queue routing. Domain modules publish a topic and a
payload and stay ignorant of Celery entirely -- the review's "do not call
.apply_async() from domain modules".

Delivery is at-least-once. A dispatcher can send the message and then die before
committing 'dispatched', so the next sweep sends it again. That is the correct
trade -- at-most-once would mean losing work, which is the bug this replaces --
and it is safe because the consumers are idempotent: ingest upserts under
deterministic point ids, delete is idempotent by design, and the security
projection re-reads current Postgres state rather than applying a delta.
"""

import asyncio
from collections.abc import Callable
from typing import Any
from uuid import UUID

import structlog

from ragz.modules.outbox import service as outbox_service
from ragz.worker.tasks import (
    build_ingest_chain,
    delete_task,
    enqueue_eval_run,
    reindex_task,
    select_queue,
)

log = structlog.get_logger()

#: Topic -> the call that actually publishes to the broker. Payload keys are
#: part of the contract with the publishing service; a change here needs the
#: matching change at the publish site and a migration for in-flight rows.
#: Handlers take (payload, queue, event_id). The queue comes from the EVENT, so the value a
#: publisher stored is the value that routes -- it used to be persisted and then
#: ignored here, which is a misleading contract: a caller could set queue= and
#: silently get default routing. documents.ingest is the exception and says so:
#: its queue is derived from file size by select_queue, not chosen by the caller.
#: The third argument is the EVENT ID, passed so a handler whose work is not
#: naturally idempotent can carry it through as an idempotency key. The fourth
#: is the Celery message headers, carrying the publishing request's W3C
#: traceparent so a worker span continues that trace rather than starting an
#: orphan one. Delivery is
#: at-least-once -- the broker send below is followed by a separate
#: mark_dispatched commit, and a crash between them redelivers the event.
_HANDLERS: dict[str, Callable[[dict[str, Any], str, UUID, dict[str, str]], None]] = {
    "documents.ingest": lambda p, _q, _e, h: build_ingest_chain(
        p["document_id"], select_queue(int(p["size_bytes"]))
    ).apply_async(headers=h),
    "documents.delete": lambda p, q, _e, h: delete_task.si(
        p["document_id"], p["actor_id"]
    ).apply_async(queue=q, headers=h),
    "documents.reindex": lambda p, q, _e, h: reindex_task.si(p["document_id"]).apply_async(
        queue=q, headers=h
    ),
    # The one non-idempotent handler: a redelivery would add a duplicate
    # EvalRun and re-spend the whole LLM/quota budget, so the runner claims the
    # event id before it scores anything.
    "evals.run": lambda p, _q, e, h: enqueue_eval_run(
        UUID(p["workspace_id"]), p["triggered_by"], e, headers=h
    ),
}


async def nudge() -> None:
    """Best-effort "start it now" after a request commits.

    The event is ALREADY durable by the time this runs, so a failure here costs
    latency, not work -- the beat sweep still delivers it. Swallowing the error
    is therefore correct: letting it escape would turn a successful upload into
    an HTTP 500 and tell the user their document failed when it is safely
    queued. Anything genuinely broken shows up as a growing outbox backlog.
    """
    try:
        await dispatch_pending()
    except Exception as exc:  # noqa: BLE001 - a nudge must never fail the request
        log.warning("outbox_nudge_failed", error=str(exc))


async def dispatch_pending(limit: int = 100) -> int:
    """Send every due event, oldest first. Returns how many were dispatched.

    One event's failure never blocks the batch: it is backed off and the sweep
    moves on, so a single malformed row cannot stall every upload in the system.
    """
    from ragz.modules.documents.ingest import _session

    dispatched = 0
    async with _session() as session:
        events = await outbox_service.claim_due(session, limit=limit)
        for event in events:
            handler = _HANDLERS.get(event.topic)
            if handler is None:
                # Unknown topic: park it rather than retry forever. This only
                # happens if a publisher ships ahead of its consumer, and it
                # needs a human, not a backoff.
                event.status = "failed"
                event.last_error = f"no handler for topic {event.topic!r}"
                log.error("outbox_unknown_topic", topic=event.topic, event_id=str(event.id))
                continue
            try:
                # to_thread, not a direct call: .apply_async() is synchronous
                # Redis I/O, and dispatch_pending runs on the FastAPI event
                # loop via nudge() after every upload/delete. A slow or
                # unreachable broker therefore stalled the whole loop -- every
                # other in-flight request with it -- while this batch's rows
                # sat locked by claim_due's FOR UPDATE. Off-loop, a broker
                # timeout costs this request latency and nothing else.
                # The publishing request's trace context, carried on the
                # Celery message so the worker span continues that trace
                # instead of starting an orphan. Empty when the event was
                # published with tracing off.
                headers = {"traceparent": event.traceparent} if event.traceparent else {}
                await asyncio.to_thread(
                    handler, event.payload, event.queue, event.id, headers
                )
            except Exception as exc:  # noqa: BLE001 - broker errors must not kill the sweep
                await outbox_service.mark_failed(session, event, str(exc))
                continue
            await outbox_service.mark_dispatched(session, event)
            dispatched += 1
        await session.commit()
    if events:
        log.info(
            "outbox_dispatched", claimed=len(events), dispatched=dispatched
        )
    return dispatched
