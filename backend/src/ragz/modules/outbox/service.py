"""Publish and claim outbox events.

Split deliberately: `publish` is the only function domain modules touch, and it
does not dispatch anything. Turning an event into a Celery message is the
worker's job (worker/outbox.py), because a domain module that knows task names
and queues is exactly the coupling the review flagged -- "do not call
.apply_async() from domain modules".
"""

from datetime import timedelta
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ragz.core.db import naive_utc
from ragz.modules.outbox.models import OutboxEvent

log = structlog.get_logger()

#: Exponential, capped. The cap matters more than the curve: a permanently bad
#: row should keep retrying slowly forever rather than be abandoned, because
#: abandoning it silently recreates the lost-work bug this table exists to fix.
_BACKOFF_SECONDS = (5, 30, 120, 600, 1800)
_MAX_BACKOFF = 3600


def _backoff(attempts: int) -> timedelta:
    idx = min(attempts, len(_BACKOFF_SECONDS) - 1)
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
    event = OutboxEvent(topic=topic, payload=payload, queue=queue)
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
    rows = await session.execute(
        select(OutboxEvent.id).where(OutboxEvent.status == "pending")
    )
    return len(list(rows.scalars()))


async def get_event(session: AsyncSession, event_id: UUID) -> OutboxEvent | None:
    return await session.get(OutboxEvent, event_id)
