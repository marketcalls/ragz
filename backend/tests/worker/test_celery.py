import pytest

from ragz.worker.celery_app import celery_app
from ragz.worker.tasks import build_ingest_chain, select_queue


def test_celery_config() -> None:
    assert celery_app.conf.task_acks_late is True
    assert {q.name for q in celery_app.conf.task_queues} == {
        "default", "interactive", "maintenance",
    }


def test_queue_selection_by_size() -> None:
    assert select_queue(5 * 1024 * 1024) == "interactive"  # < 50 MB jumps the queue
    assert select_queue(10 * 1024 * 1024) == "interactive"
    assert select_queue(60 * 1024 * 1024) == "default"


def test_ingest_chain_structure() -> None:
    sig = build_ingest_chain("doc-id-123", "interactive")
    names = [t.task for t in sig.tasks]
    assert names == ["documents.parse", "documents.chunk", "documents.embed_upsert"]
    assert all(t.options.get("queue") == "interactive" for t in sig.tasks)
    assert all(t.args == ("doc-id-123",) for t in sig.tasks)


def test_tasks_module_included_for_standalone_worker() -> None:
    """A real `celery -A ...celery_app worker` must import tasks.py (smoke regression)."""
    from ragz.worker.celery_app import celery_app

    assert "ragz.worker.tasks" in celery_app.conf.include


def test_every_scheduled_task_runs_on_the_maintenance_queue() -> None:
    """Phase 2 item 5: the beat-scheduled safety nets must not queue behind
    bulk ingestion.

    The outbox sweep exists to bound how late owed work can start. Sharing the
    "default" queue with a multi-minute embed meant a 30-second sweep could wait
    minutes -- defeating the guarantee it exists to provide.
    """
    from ragz.worker.celery_app import celery_app

    schedule = celery_app.conf.beat_schedule
    assert schedule, "no beat entries -- this test would pass vacuously"
    misrouted = {
        name: entry.get("options", {}).get("queue")
        for name, entry in schedule.items()
        if entry.get("options", {}).get("queue") != "maintenance"
    }
    assert misrouted == {}
    assert {q.name for q in celery_app.conf.task_queues} == {
        "default", "interactive", "maintenance",
    }


def test_task_time_limits_are_set_and_maintenance_is_tighter() -> None:
    """Nothing bounded task runtime before this: a hung parse or embed held its
    worker slot indefinitely, and with task_acks_late a killed worker
    re-delivers it, so one poisonous document could occupy a slot repeatedly.

    Bulk tasks inherit the app-level default (their per-task attribute stays
    None); maintenance tasks override it with a much tighter pair, because
    small bounded work running for minutes is stuck rather than busy.
    """
    from ragz.worker import tasks as worker_tasks
    from ragz.worker.celery_app import celery_app

    soft = celery_app.conf.task_soft_time_limit
    hard = celery_app.conf.task_time_limit
    assert soft and hard and hard > soft, "a hard limit must outlast the soft one"

    # Bulk: no per-task override, so the generous app default applies.
    assert worker_tasks.parse_task.soft_time_limit is None
    assert worker_tasks.embed_upsert_task.soft_time_limit is None

    # Maintenance: explicitly tighter than the bulk default.
    for task in (worker_tasks.outbox_dispatch_task, worker_tasks.outbox_purge_task,
                 worker_tasks.reconcile_stuck_documents_task):
        assert task.soft_time_limit is not None
        assert task.soft_time_limit < soft
        assert task.time_limit > task.soft_time_limit


def test_a_soft_timeout_is_terminal_rather_than_retried() -> None:
    """SoftTimeLimitExceeded is an ordinary Exception, so _run's generic retry
    would give a task that is merely too slow three MORE full-length attempts --
    four times the wasted work, on the very worker slot the limit exists to
    free."""
    from celery.exceptions import SoftTimeLimitExceeded

    from ragz.worker.tasks import _run

    class _FakeTask:
        request = type("R", (), {"retries": 0})()

        def retry(self, **_kw: object) -> Exception:  # pragma: no cover - must not run
            raise AssertionError("a soft timeout must not be retried")

    async def _timeout() -> None:
        raise SoftTimeLimitExceeded

    with pytest.raises(SoftTimeLimitExceeded):
        _run(_FakeTask(), _timeout)
