"""Transactional outbox: durable intent to do background work (review P1).

The bug this replaces: services committed domain state and THEN called
.apply_async(). A crash or broker outage between those two points left a
document row at "queued" with no message anywhere -- durable state, no durable
work, and no record that anything was owed. Retrying the HTTP request is not a
fix and a status column is not a queue.

These tests hold the guarantee: the event commits WITH the domain change, a
broker failure never loses it, and the sweep eventually delivers it.
"""

from datetime import datetime
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ragz.core.db import naive_utc
from ragz.modules.auth.models import User
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
    # Handlers take (payload, queue, event_id) -- the id is there so a handler
    # whose work is not idempotent can use it as an idempotency key.
    monkeypatch.setitem(
        worker_outbox._HANDLERS, "documents.ingest", lambda p, _q, _e: sent.append(p)
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
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A publisher shipped ahead of its consumer needs attention, not backoff.

    Driven through the REAL dispatcher: an earlier version set status='failed'
    by hand, which re-implemented the branch under test instead of testing it --
    it would still have passed if that branch were deleted entirely.
    """
    from ragz.modules.documents import ingest
    from ragz.worker import outbox as worker_outbox

    monkeypatch.setattr(ingest, "_session", _FixedSession(session))

    outbox_service.publish(session, topic="totally.unknown", payload={})
    await session.commit()

    assert await worker_outbox.dispatch_pending() == 0

    # Parked, not backed off: it will not be retried, and it no longer counts
    # as owed work, so it cannot mask a real backlog.
    assert await outbox_service.claim_due(session) == []
    assert await outbox_service.pending_backlog(session) == 0
    event = (await session.execute(select(OutboxEvent))).scalars().one()
    await session.refresh(event)
    assert event.status == "failed"
    assert "totally.unknown" in (event.last_error or "")



async def _seed_document(
    session: AsyncSession, user: "User", *, status: str, updated_at: "datetime"
):  # type: ignore[no-untyped-def]
    """A document in a given pipeline state, with a controllable updated_at --
    the field the stuck sweep keys off."""
    from ragz.modules.documents.models import Document
    from ragz.modules.tenancy.models import Workspace, WorkspaceMember

    ws = Workspace(org_id=user.org_id, name=f"stuck-{uuid4().hex[:6]}")
    session.add(ws)
    await session.flush()
    session.add(WorkspaceMember(workspace_id=ws.id, user_id=user.id))
    doc = Document(
        org_id=user.org_id, workspace_id=ws.id, filename="f.pdf",
        mime="application/pdf", size_bytes=10, content_hash=uuid4().hex,
        status=status, storage_key="k", created_by=user.id, lineage_id=uuid4(),
    )
    session.add(doc)
    await session.commit()
    # Set after the insert: updated_at has onupdate=naive_utc, so assigning it
    # in the constructor would be overwritten on the very next flush.
    doc.updated_at = updated_at
    await session.commit()
    return doc


async def test_a_stuck_document_is_requeued_through_the_outbox(
    session: AsyncSession, seeded_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rows stranded mid-pipeline must come back.

    The outbox stops work being lost from now on, but it cannot help a document
    stranded BEFORE it existed, or one whose worker died after claiming the
    message. Those sit at "queued"/"processing"/"deleting" forever, because the
    status column looks like a queue while nothing reads it.

    Recovery republishes to the OUTBOX rather than to Celery directly, so the
    re-drive carries the same durability guarantee as the original.
    """
    from datetime import timedelta

    from ragz.modules.documents import ingest

    monkeypatch.setattr(ingest, "_session", _FixedSession(session))

    stale = naive_utc() - timedelta(seconds=ingest._STUCK_AFTER_SECONDS + 60)
    doc = await _seed_document(session, seeded_user, status="queued", updated_at=stale)

    counts = await ingest.reconcile_stuck_documents()

    assert counts["queued"] == 1
    due = await outbox_service.claim_due(session)
    assert [e.topic for e in due] == ["documents.ingest"]
    assert due[0].payload["document_id"] == str(doc.id)


async def test_a_recently_updated_document_is_left_alone(
    session: AsyncSession, seeded_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A slow parse is not a stuck one -- re-driving it would duplicate work."""
    from ragz.modules.documents import ingest

    monkeypatch.setattr(ingest, "_session", _FixedSession(session))

    await _seed_document(session, seeded_user, status="processing", updated_at=naive_utc())

    counts = await ingest.reconcile_stuck_documents()

    assert counts == {"queued": 0, "processing": 0, "deleting": 0}
    assert await outbox_service.claim_due(session) == []


async def test_the_stuck_sweep_does_not_republish_the_same_document_every_hour(
    session: AsyncSession, seeded_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cubic P2: the recovery mechanism must not become an amplifier.

    Without a lease, every sweep re-publishes the same stranded document,
    stacking one outbox event per hour until its status finally changes.
    Stamping updated_at makes the row ineligible until another full cutoff has
    elapsed, so it is retried at most once per cutoff.
    """
    from datetime import timedelta

    from ragz.modules.documents import ingest

    monkeypatch.setattr(ingest, "_session", _FixedSession(session))
    stale = naive_utc() - timedelta(seconds=ingest._STUCK_AFTER_SECONDS + 60)
    await _seed_document(session, seeded_user, status="queued", updated_at=stale)

    assert (await ingest.reconcile_stuck_documents())["queued"] == 1
    assert len(await outbox_service.claim_due(session)) == 1

    # Immediately re-running must find nothing: the lease has not expired.
    assert (await ingest.reconcile_stuck_documents())["queued"] == 0
    assert len(await outbox_service.claim_due(session)) == 1, "no duplicate event"


async def test_a_live_worker_mid_stage_is_not_republished(
    session: AsyncSession, seeded_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cubic P1: a slow stage must not look like a dead worker.

    Nothing touches the DOCUMENT row while a stage runs -- run_embed_upsert
    commits IngestJob.progress per batch and leaves the document alone. So a
    parse+embed genuinely running for longer than the cutoff had a stale
    Document.updated_at and was re-published while still working, duplicating
    the whole embed. Liveness is the newest IngestJob heartbeat instead.
    """
    from datetime import timedelta

    from ragz.modules.documents import ingest
    from ragz.modules.documents.models import IngestJob

    monkeypatch.setattr(ingest, "_session", _FixedSession(session))
    stale = naive_utc() - timedelta(seconds=ingest._STUCK_AFTER_SECONDS + 60)
    doc = await _seed_document(session, seeded_user, status="processing", updated_at=stale)

    # The worker is alive: it committed a batch a moment ago.
    session.add(IngestJob(document_id=doc.id, stage="embed", progress=0.4,
                          started_at=stale, updated_at=naive_utc()))
    await session.commit()

    counts = await ingest.reconcile_stuck_documents()

    assert counts == {"queued": 0, "processing": 0, "deleting": 0}
    assert await outbox_service.claim_due(session) == []


async def test_a_document_whose_worker_died_mid_stage_is_still_requeued(
    session: AsyncSession, seeded_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other half: a heartbeat that stopped is exactly what recovery is for,
    so keying off IngestJob must not make dead workers unrecoverable."""
    from datetime import timedelta

    from ragz.modules.documents import ingest
    from ragz.modules.documents.models import IngestJob

    monkeypatch.setattr(ingest, "_session", _FixedSession(session))
    stale = naive_utc() - timedelta(seconds=ingest._STUCK_AFTER_SECONDS + 60)
    doc = await _seed_document(session, seeded_user, status="processing", updated_at=stale)

    # Started, then went silent: the heartbeat is as old as the document.
    session.add(IngestJob(document_id=doc.id, stage="embed", progress=0.4,
                          started_at=stale, updated_at=stale))
    await session.commit()

    counts = await ingest.reconcile_stuck_documents()

    assert counts["processing"] == 1
    due = await outbox_service.claim_due(session)
    assert [e.payload["document_id"] for e in due] == [str(doc.id)]


async def test_a_stranded_delete_replays_with_the_real_requester_not_the_creator(
    session: AsyncSession, seeded_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cubic P2: the replayed audit must not name an unrelated user.

    The creator of a document is frequently not the person who asked for it to
    be deleted. Replaying documents.delete with created_by wrote a
    document.deleted audit naming the uploader -- a false record in an
    append-only log.
    """
    from datetime import timedelta

    from ragz.modules.auth.models import User as UserModel
    from ragz.modules.documents import ingest

    monkeypatch.setattr(ingest, "_session", _FixedSession(session))
    stale = naive_utc() - timedelta(seconds=ingest._STUCK_AFTER_SECONDS + 60)
    doc = await _seed_document(session, seeded_user, status="deleting", updated_at=stale)

    # A DIFFERENT user asked for the deletion.
    deleter = UserModel(
        org_id=seeded_user.org_id, email=f"deleter-{uuid4().hex[:6]}@x.com",
        password_hash="x", role="admin",  # noqa: S106
    )
    session.add(deleter)
    await session.flush()
    doc.deleted_by = deleter.id
    await session.commit()
    # Restore staleness AFTER that commit, exactly as _seed_document does:
    # onupdate=naive_utc refreshed updated_at when deleted_by was written, and
    # re-assigning the same value in that same flush is not a change, so it has
    # to be a second update or the row no longer looks stranded.
    doc.updated_at = stale
    await session.commit()
    deleter_id = deleter.id
    creator_id = seeded_user.id

    assert (await ingest.reconcile_stuck_documents())["deleting"] == 1

    due = await outbox_service.claim_due(session)
    assert [e.topic for e in due] == ["documents.delete"]
    assert due[0].payload["actor_id"] == str(deleter_id)
    assert due[0].payload["actor_id"] != str(creator_id)


async def test_a_legacy_stranded_delete_replays_with_no_actor_rather_than_a_wrong_one(
    session: AsyncSession, seeded_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rows that entered "deleting" before deleted_by existed have no
    recoverable actor. NULL is honest; created_by would be a false attribution.
    run_delete and record_audit both accept a null actor."""
    from datetime import timedelta

    from ragz.modules.documents import ingest

    monkeypatch.setattr(ingest, "_session", _FixedSession(session))
    stale = naive_utc() - timedelta(seconds=ingest._STUCK_AFTER_SECONDS + 60)
    await _seed_document(session, seeded_user, status="deleting", updated_at=stale)
    await session.commit()

    assert (await ingest.reconcile_stuck_documents())["deleting"] == 1

    due = await outbox_service.claim_due(session)
    assert due[0].payload["actor_id"] is None
