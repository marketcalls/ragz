"""Plan J Task 12: the nightly eval fan-out. Mirrors the existing task tests
in test_celery.py for structure, but this is the first worker task that
touches a real DB from within its own asyncio.run()-wrapped closure, so the
seeding here goes through the `session`/`stack_env` fixtures (same
committed-data-is-visible-to-a-second-connection pattern modules/evals uses)
and the sync Celery task is invoked from a worker thread (asyncio.to_thread)
so its internal asyncio.run() never collides with the test's own running
event loop."""

import asyncio

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from raghub.modules.auth.models import User
from raghub.modules.evals.models import GoldenQuery
from raghub.modules.tenancy.models import Organization, Workspace
from raghub.worker import tasks


async def test_run_all_workspaces_enqueues_only_workspaces_with_golden_queries(
    session: AsyncSession, stack_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    org = Organization(name="WorkerEvalOrg")
    session.add(org)
    await session.flush()
    user = User(
        org_id=org.id, email="worker@evalorg.com", password_hash="x", role="admin"  # noqa: S106
    )
    ws_a = Workspace(org_id=org.id, name="ws-a")
    ws_b = Workspace(org_id=org.id, name="ws-b")  # no golden queries -- must be skipped
    session.add_all([user, ws_a, ws_b])
    await session.flush()
    session.add(GoldenQuery(workspace_id=ws_a.id, question="q", created_by=user.id))
    await session.commit()

    enqueued: list[tuple[object, str]] = []
    monkeypatch.setattr(
        "raghub.worker.tasks.enqueue_eval_run",
        lambda workspace_id, triggered_by: enqueued.append((workspace_id, triggered_by)),
    )

    await asyncio.to_thread(tasks.run_all_workspaces_task)

    assert enqueued == [(ws_a.id, "nightly")]
