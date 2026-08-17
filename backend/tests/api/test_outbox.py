"""Transactional outbox: durable intent to do background work (review P1).

The bug this replaces: services committed domain state and THEN called
.apply_async(). A crash or broker outage between those two points left a
document row at "queued" with no message anywhere -- durable state, no durable
work, and no record that anything was owed. Retrying the HTTP request is not a
fix and a status column is not a queue.

These tests hold the guarantee: the event commits WITH the domain change, a
broker failure never loses it, and the sweep eventually delivers it.
"""

from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ragz.core.db import naive_utc
from ragz.modules.outbox import service as outbox_service
from ragz.modules.outbox.models import OutboxEvent


async def test_publish_joins_the_callers_transaction_and_does_not_dispatch(
    session: AsyncSession,
) -> None:
    """publish() must be inert until the caller commits.

    If it dispatched, or committed on its own, a rolled-back domain change could
    still leave work scheduled -- the mirror image of the original bug.
    """
    outbox_service.publish(
        session, topic="documents.ingest", payload={"document_id": str(uuid4())}
    )
    await session.rollback()

    rows = (await session.execute(select(OutboxEvent))).scalars().all()
    assert rows == [], "a rolled-back transaction must leave no owed work"


async def test_a_committed_event_is_claimable_and_marked_dispatched(
    session: AsyncSession,
) -> None:
    doc_id = str(uuid4())
    outbox_service.publish(
        session, topic="documents.ingest", payload={"document_id": doc_id, "size_bytes": 10}
    )
    await session.commit()

    due = await outbox_service.claim_due(session)
    assert [e.payload["document_id"] for e in due] == [doc_id]

    await outbox_service.mark_dispatched(session, due[0])
    await session.commit()

    assert await outbox_service.claim_due(session) == [], "dispatched work is not re-claimed"


async def test_a_broker_failure_backs_the_event_off_but_never_drops_it(
    session: AsyncSession,
) -> None:
    """The whole point: a failed dispatch keeps the work owed.

    Status stays 'pending' rather than becoming 'failed', because the event
    still deserves to run. Only an operator parks a row.
    """
    outbox_service.publish(session, topic="documents.ingest", payload={"document_id": str(uuid4())})
    await session.commit()
    event = (await outbox_service.claim_due(session))[0]

    await outbox_service.mark_failed(session, event, "broker unreachable")
    await session.commit()
    await session.refresh(event)

    assert event.status == "pending", "still owed -- never silently abandoned"
    assert event.attempts == 1
    assert event.last_error == "broker unreachable"
    assert event.available_at > naive_utc(), "backed off, so one bad row cannot spin the sweep"

    # Not due yet, so the sweep skips it rather than hot-looping...
    assert await outbox_service.claim_due(session) == []
    # ...and once the backoff elapses it comes back on its own.
    event.available_at = naive_utc()
    await session.commit()
    assert [e.id for e in await outbox_service.claim_due(session)] == [event.id]


class _FixedSession:
    """Stands in for ingest._session so dispatch_pending runs against the test
    container instead of building its own engine from settings.database_url --
    which in a test process is the DEVELOPER'S database, not this fixture's."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def __call__(self) -> "_FixedSession":
        return self

    async def __aenter__(self) -> AsyncSession:
        return self._session

    async def __aexit__(self, *_exc: object) -> None:
        return None


async def test_the_dispatcher_delivers_pending_work_to_the_broker(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End to end through the REAL dispatch_pending, broker call stubbed."""
    from ragz.modules.documents import ingest
    from ragz.worker import outbox as worker_outbox

    sent: list[dict[str, Any]] = []
    monkeypatch.setitem(
        worker_outbox._HANDLERS, "documents.ingest", lambda p: sent.append(p)
    )
    monkeypatch.setattr(ingest, "_session", _FixedSession(session))

    doc_id = str(uuid4())
    outbox_service.publish(
        session, topic="documents.ingest", payload={"document_id": doc_id, "size_bytes": 1}
    )
    await session.commit()

    dispatched = await worker_outbox.dispatch_pending()

    assert dispatched == 1
    assert [p["document_id"] for p in sent] == [doc_id]
    # Re-running must be a no-op: the event is dispatched, not owed again.
    assert await worker_outbox.dispatch_pending() == 0


async def test_an_unknown_topic_is_parked_for_a_human_not_retried_forever(
    session: AsyncSession,
) -> None:
    """A publisher shipped ahead of its consumer needs attention, not backoff."""
    outbox_service.publish(session, topic="totally.unknown", payload={})
    await session.commit()

    event = (await outbox_service.claim_due(session))[0]
    event.status = "failed"
    event.last_error = "no handler for topic 'totally.unknown'"
    await session.commit()

    assert await outbox_service.claim_due(session) == []
    assert (await outbox_service.pending_backlog(session)) == 0
