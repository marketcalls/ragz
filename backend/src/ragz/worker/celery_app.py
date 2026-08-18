from celery import Celery
from celery.signals import worker_process_init, worker_process_shutdown
from kombu import Queue

from ragz.core.config import get_settings
from ragz.modules.chat.prompting import warm_token_encoder


@worker_process_init.connect  # type: ignore[untyped-decorator]  # blinker Signal.connect
def _warm_token_encoder_on_worker_start(**kwargs: object) -> None:
    """Sync hook (no event loop here): primes tiktoken's encoding cache once
    per worker process, off the task path, mirroring the API's startup
    warmup. Never raises - see warm_token_encoder."""
    warm_token_encoder()
    # ADR-0006: create this process's task loop up front rather than on the
    # first task, so the cost lands at startup and every task afterwards
    # shares one engine (see worker/loop.py and core.db.get_loop_engine).
    from ragz.worker.loop import get_loop

    get_loop()


@worker_process_shutdown.connect  # type: ignore[untyped-decorator]  # blinker Signal.connect
def _close_worker_loop(**kwargs: object) -> None:
    """Dispose the process-lifetime engine and close the loop.

    Without this a worker exits still holding Postgres connections, which is
    the cost of keeping them open across tasks in the first place.
    """
    from ragz.worker.loop import shutdown

    shutdown()


def build_celery() -> Celery:
    settings = get_settings()
    app = Celery(
        "ragz",
        broker=settings.redis_url,
        backend=settings.redis_url,
        # Without include, a real `celery -A ...celery_app worker` process never
        # imports tasks.py and rejects every task as unregistered (found by smoke).
        include=["ragz.worker.tasks"],
    )
    app.conf.update(
        task_acks_late=True,  # a killed worker re-delivers, pairs with idempotent upserts
        worker_prefetch_multiplier=1,  # long tasks: no hoarding
        task_default_queue="default",
        # "maintenance" is separate so the beat-scheduled safety nets do not
        # queue behind bulk ingestion. The outbox sweep exists to bound how late
        # owed work can start; sharing a queue with a multi-minute embed meant a
        # 30s sweep could wait minutes, defeating its own purpose. Workers must
        # consume it -- see the -Q argument in README.md.
        task_queues=(Queue("default"), Queue("interactive"), Queue("maintenance")),
        # Default limits apply to the bulk/ingest family; maintenance tasks
        # override them per-task with the tighter pair.
        task_soft_time_limit=settings.celery_soft_time_limit_seconds,
        task_time_limit=settings.celery_time_limit_seconds,
        broker_connection_retry_on_startup=True,
        # Plan G Task 12 (MODEL-10/G7): daily catalog sync; the 3-day cache
        # inside refresh_catalog makes retries/redundant runs cheap.
        beat_schedule={
            "refresh-model-catalog": {
                "task": "models.refresh_catalog",
                "options": {"queue": "maintenance"},
                "schedule": 24 * 60 * 60,
            },
            # Task 12 (Plan J, §6): nightly eval fan-out, same interval-seconds
            # style as the entry above (not a crontab).
            "nightly-eval-run": {
                "task": "evals.run_all_workspaces",
                "options": {"queue": "maintenance"},
                "schedule": 24 * 60 * 60,
            },
            # Task 7 (DOC-9): daily TTL sweep for ephemeral chat attachments,
            # same interval-seconds style as the two entries above.
            "attachment-ttl-cleanup": {
                "task": "attachments.cleanup_stale",
                "options": {"queue": "maintenance"},
                "schedule": 24 * 60 * 60,
            },
            # Review P0: fail-closed ACL projection hides a document when Qdrant
            # is unreachable rather than over-sharing it. This reopens the door
            # once the store is back. Minutes, not the daily cadence above --
            # the window it closes is one where legitimate readers are denied,
            # so the recovery time IS the user-visible outage.
            "reconcile-security-projections": {
                "task": "documents.reconcile_security_projections",
                "options": {"queue": "maintenance"},
                "schedule": 5 * 60,
            },
            # Review P1: the safety net behind the API's inline nudge. Frequent,
            # because the gap it covers is work that is durably owed but not yet
            # started -- an upload sitting at "queued". 30s bounds how late a
            # job can be when the nudge could not run.
            "outbox-dispatch": {
                "task": "outbox.dispatch_pending",
                "options": {"queue": "maintenance"},
                "schedule": 30,
            },
            # Cubic P2: bounded retention for the same table. Daily, because
            # the window is 7 days -- sweeping more often would just take
            # locks on the table the dispatcher reads every 30s for nothing.
            "outbox-purge": {
                "task": "outbox.purge_dispatched",
                "options": {"queue": "maintenance"},
                "schedule": 24 * 60 * 60,
            },
            # Review P1: recovers documents stranded mid-pipeline -- rows from
            # before the outbox existed, and any whose worker died after
            # claiming the message. Hourly, because its own cutoff is hours: a
            # tighter schedule would only re-scan the same rows.
            "reconcile-stuck-documents": {
                "task": "documents.reconcile_stuck",
                "options": {"queue": "maintenance"},
                "schedule": 60 * 60,
            },
        },
    )
    return app


celery_app = build_celery()
