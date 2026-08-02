from ragz.worker.celery_app import celery_app
from ragz.worker.tasks import build_ingest_chain, select_queue


def test_celery_config() -> None:
    assert celery_app.conf.task_acks_late is True
    assert {q.name for q in celery_app.conf.task_queues} == {"default", "interactive"}


def test_queue_selection_by_size() -> None:
    assert select_queue(5 * 1024 * 1024) == "interactive"  # < 10 MB jumps the queue
    assert select_queue(10 * 1024 * 1024) == "default"
    assert select_queue(50 * 1024 * 1024) == "default"


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
