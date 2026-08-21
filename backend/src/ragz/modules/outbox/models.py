"""Transactional outbox: durable intent to do background work.

Architecture review P1. Domain services committed state and THEN called
`.apply_async()`. A broker outage or a crash between those two points left
durable database state with no durable work behind it -- an upload stuck at
"queued" forever, a delete stuck at "deleting", with nothing to retry from.
Retrying the HTTP request is not a fix, and a status column is not a queue.

An outbox row is written INSIDE the caller's transaction, so the domain change
and the intent to act on it commit together or not at all. A separate dispatcher
turns rows into Celery messages; the worst case becomes "the job runs late",
never "the job is lost".
"""

from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, Index, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ragz.core.db import Base, UUIDPk, naive_utc


class OutboxEvent(UUIDPk, Base):
    __tablename__ = "outbox_events"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'dispatched', 'failed')",
            name="ck_outbox_events_status",
        ),
        # The dispatcher's only query: due pending work, oldest first. Partial,
        # because dispatched rows are the overwhelming majority and are never
        # read by it.
        Index(
            "ix_outbox_events_due",
            "available_at",
            postgresql_where=text("status = 'pending'"),
        ),
        # The retention sweep's only query. Partial for the same reason as the
        # one above, inverted: it reads exactly the dispatched rows the
        # dispatcher never looks at, and without this the daily purge would seq
        # scan the largest table in the database.
        Index(
            "ix_outbox_events_dispatched_at",
            "dispatched_at",
            postgresql_where=text("status = 'dispatched'"),
        ),
    )

    #: Logical event name, mapped to a Celery task by the worker's registry.
    #: Domain modules name intent; they do not know task names or queues exist.
    topic: Mapped[str]
    #: Task kwargs. JSONB, so a payload shape change is visible in migrations
    #: rather than hidden inside a pickled message.
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    queue: Mapped[str] = mapped_column(default="default")
    status: Mapped[str] = mapped_column(default="pending")  # pending|dispatched|failed
    attempts: Mapped[int] = mapped_column(default=0)
    last_error: Mapped[str | None] = mapped_column(default=None)
    #: Backoff gate. A failed dispatch is retried from here, so one poisonous
    #: row cannot spin the dispatcher.
    available_at: Mapped[datetime] = mapped_column(default=naive_utc)
    dispatched_at: Mapped[datetime | None] = mapped_column(default=None)
    #: W3C traceparent of the request that published this event, captured at
    #: publish time rather than at dispatch. Dispatch can happen much later and
    #: in another process (the beat sweep after a crash or a backoff), so a
    #: traceparent taken there would parent the work to whichever sweep
    #: happened to pick it up rather than to the request that caused it --
    #: which is exactly the causal link the async ingestion path needs to be
    #: traceable end to end. NULL when tracing is off or the publisher ran
    #: outside a span; consumers then simply start their own trace.
    traceparent: Mapped[str | None] = mapped_column(default=None)
