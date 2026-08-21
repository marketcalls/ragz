"""Publish and claim outbox events.

Split deliberately: `publish` is the only function domain modules touch, and it
does not dispatch anything. Turning an event into a Celery message is the
worker's job (worker/outbox.py), because a domain module that knows task names
and queues is exactly the coupling the review flagged -- "do not call
.apply_async() from domain modules".
"""

from datetime import timedelta
from typing import Any

import structlog
from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ragz.core.db import naive_utc
from ragz.core.tracing import inject_context
from ragz.modules.outbox.models import OutboxEvent

log = structlog.get_logger()

#: Exponential, capped. The cap matters more than the curve: a permanently bad
#: row should keep retrying slowly forever rather than be abandoned, because
#: abandoning it silently recreates the lost-work bug this table exists to fix.
_BACKOFF_SECONDS = (5, 30, 120, 600, 1800)
_MAX_BACKOFF = 3600


def _backoff(attempts: int) -> timedelta:
    """Delay before retry number `attempts` (1-based).

    Indexed with attempts-1: mark_failed increments FIRST, so the first failure
    arrives here as attempts=1 and must get the 5s tier, not the 30s one.
    """
    idx = min(max(attempts - 1, 0), len(_BACKOFF_SECONDS) - 1)
    return timedelta(seconds=min(_BACKOFF_SECONDS[idx], _MAX_BACKOFF))


def publish(
    session: AsyncSession,
    *,
    topic: str,
    payload: dict[str, Any],
    queue: str = "default",
) -> OutboxEvent:
    """Record intent to run background work, in the CALLER's transaction.

    Deliberately not async and deliberately not committing: it only adds to the
    session, so the event lands in the same commit as the domain change that
    justifies it. Commit both or neither -- that is the entire point. A caller
    that commits and then publishes has reintroduced the bug.
    """
    # Capture the trace context HERE, in the caller's request, not at dispatch.
    # Dispatch may happen much later and in another process (the beat sweep
    # after a crash, or after a backoff), so a traceparent taken there would
    # parent the work to whichever sweep collected it instead of to the request
    # that caused it. Empty dict when tracing is off or there is no active
    # span, which stores NULL and lets the consumer start its own trace.
    event = OutboxEvent(
        topic=topic,
        payload=payload,
        queue=queue,
        traceparent=inject_context({}).get("traceparent"),
    )
    session.add(event)
    return event


async def claim_due(session: AsyncSession, *, limit: int = 100) -> list[OutboxEvent]:
    """Lock a batch of due events for this dispatcher instance.

    FOR UPDATE SKIP LOCKED so several dispatchers (or an API-side nudge racing
    the beat sweep) never dispatch the same row twice and never block each
    other. Rows stay 'pending' until the dispatch actually succeeds, so a
    dispatcher that dies mid-batch simply releases its locks and the next sweep
    picks the work up.
    """
    rows = await session.execute(
        select(OutboxEvent)
        .where(
            OutboxEvent.status == "pending",
            OutboxEvent.available_at <= naive_utc(),
        )
        .order_by(OutboxEvent.available_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    return list(rows.scalars())


async def mark_dispatched(session: AsyncSession, event: OutboxEvent) -> None:
    event.status = "dispatched"
    event.dispatched_at = naive_utc()
    event.last_error = None


async def mark_failed(session: AsyncSession, event: OutboxEvent, error: str) -> None:
    """Record the failure and back off; never drop the row.

    Status stays 'pending' rather than moving to 'failed': the event is still
    owed work. 'failed' is reserved for rows an operator has explicitly parked,
    so the dispatcher's own errors can never quietly become data loss.
    """
    event.attempts += 1
    event.last_error = error[:1000]
    event.available_at = naive_utc() + _backoff(event.attempts)
    log.warning(
        "outbox_dispatch_failed",
        event_id=str(event.id),
        topic=event.topic,
        attempts=event.attempts,
        retry_in_seconds=_backoff(event.attempts).total_seconds(),
    )


async def pending_backlog(session: AsyncSession) -> int:
    """How many events are owed work. Surfaced by ops/health so a broker outage
    shows up as a growing number rather than as silence."""
    # COUNT(*) in the database: this is called for health/alerting, exactly when
    # the backlog is LARGE, so materialising every pending UUID to len() them
    # would be slowest precisely when it matters most.
    count = await session.scalar(
        select(func.count()).select_from(OutboxEvent).where(OutboxEvent.status == "pending")
    )
    return int(count or 0)


#: How long a successfully dispatched event is kept before purging. Long enough
#: to debug "did that actually fire?" against a recent incident, short enough
#: that the table does not become the largest one in the database.
_RETENTION = timedelta(days=7)


async def purge_dispatched(
    session: AsyncSession, *, older_than: timedelta = _RETENTION, limit: int = 10_000
) -> int:
    """Delete dispatched events past their retention window. Returns the count.

    Every successful dispatch left its row behind forever, so outbox_events grew
    without bound in storage, backups and vacuum work -- and it is the busiest
    insert path in the system, one row per upload/delete/reindex/eval.

    ONLY status='dispatched' is purged. A 'pending' row is owed work, and a
    'failed' one was parked for a human to look at -- deleting either would
    destroy the durability guarantee this whole module exists to provide.

    Batched via a subquery on the primary key: an unbounded DELETE over a large
    backlog holds locks for as long as it takes, on the table the dispatcher
    needs every 30 seconds.
    """
    cutoff = naive_utc() - older_than
    doomed = (
        select(OutboxEvent.id)
        .where(OutboxEvent.status == "dispatched", OutboxEvent.dispatched_at < cutoff)
        .limit(limit)
        .scalar_subquery()
    )
    result = await session.execute(
        sa_delete(OutboxEvent).where(OutboxEvent.id.in_(doomed)).returning(OutboxEvent.id)
    )
    purged = len(result.scalars().all())
    await session.commit()
    if purged:
        log.info("outbox_purged", purged=purged, older_than_days=older_than.days)
    return purged
