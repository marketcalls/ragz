"""eval run dispatch idempotency

Outbox delivery is at-least-once: dispatch_pending hands the message to the
broker and only then commits mark_dispatched, so a crash between the two leaves
the event pending and it is redelivered. Every other topic is idempotent by
construction -- ingest upserts deterministic point ids, delete is idempotent --
but evals.run is not: run_eval inserted a fresh EvalRun per delivery, so one
manual trigger could duplicate run history AND re-spend the entire LLM/quota
budget for the workspace (Cubic P1).

eval_runs.dispatch_id records which outbox event produced the run, UNIQUE so a
second delivery of the same event loses the race and is skipped. Nullable
because runs with no event behind them (the nightly fan-out, direct calls) have
nothing to key on, and Postgres does not collide NULLs in a unique index --
those callers keep their existing behaviour exactly.

Revision ID: 81d69f832405
Revises: d21fa2c66844
Create Date: 2026-08-18

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '81d69f832405'
down_revision: Union[str, Sequence[str], None] = 'd21fa2c66844'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("eval_runs", sa.Column("dispatch_id", sa.Uuid(), nullable=True))
    # Existing rows keep NULL: they predate the key and must not collide.
    op.create_unique_constraint("uq_eval_runs_dispatch_id", "eval_runs", ["dispatch_id"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint("uq_eval_runs_dispatch_id", "eval_runs", type_="unique")
    op.drop_column("eval_runs", "dispatch_id")
