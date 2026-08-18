from datetime import datetime
from uuid import UUID

from sqlalchemy import ForeignKey, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from ragz.core.db import Base, UUIDPk, naive_utc


class GoldenQuery(UUIDPk, Base):
    """Phase 3 eval harness (§6): an admin-authored question with the
    document(s) it should retrieve. Owned by a workspace; deleted with it."""

    __tablename__ = "golden_queries"

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    question: Mapped[str] = mapped_column(Text())
    # Empty list is valid: an off-corpus probe exercising the fallback/decline
    # path, not just the happy path.
    expected_document_ids: Mapped[list[UUID]] = mapped_column(
        ARRAY(PG_UUID(as_uuid=True)), default=list
    )
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(default=naive_utc)


class EvalRun(UUIDPk, Base):
    """Phase 3 eval harness (§6): one row per runner invocation
    (modules/evals/runner.py::run_eval) — hit-rate/citation-precision always
    computed, avg_faithfulness only when a utility model is designated."""

    __tablename__ = "eval_runs"
    __table_args__ = (
        # Idempotency key for outbox delivery. The dispatcher sends to the
        # broker and only then marks the event dispatched; a crash in between
        # leaves it pending and it is redelivered. Most topics are idempotent
        # by construction, but an eval run is not -- a second delivery would
        # add a duplicate row AND re-spend the LLM/quota budget. UNIQUE turns
        # the second claim into an IntegrityError the runner treats as "already
        # handled". NULL for runs with no outbox event behind them (the nightly
        # fan-out and direct calls), and NULLs do not collide in Postgres.
        UniqueConstraint("dispatch_id", name="uq_eval_runs_dispatch_id"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    dispatch_id: Mapped[UUID | None] = mapped_column(default=None)
    triggered_by: Mapped[str] = mapped_column(default="manual")  # manual|settings_change|nightly
    query_count: Mapped[int] = mapped_column(default=0)
    hit_rate: Mapped[float | None] = mapped_column(default=None)
    citation_precision: Mapped[float | None] = mapped_column(default=None)
    avg_faithfulness: Mapped[float | None] = mapped_column(default=None)  # 1-5 scale
    created_at: Mapped[datetime] = mapped_column(default=naive_utc)
