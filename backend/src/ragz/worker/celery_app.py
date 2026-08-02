from celery import Celery
from celery.signals import worker_process_init
from kombu import Queue

from ragz.core.config import get_settings
from ragz.modules.chat.prompting import warm_token_encoder


@worker_process_init.connect  # type: ignore[untyped-decorator]  # blinker Signal.connect
def _warm_token_encoder_on_worker_start(**kwargs: object) -> None:
    """Sync hook (no event loop here): primes tiktoken's encoding cache once
    per worker process, off the task path, mirroring the API's startup
    warmup. Never raises - see warm_token_encoder."""
    warm_token_encoder()


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
        task_queues=(Queue("default"), Queue("interactive")),
        broker_connection_retry_on_startup=True,
        # Plan G Task 12 (MODEL-10/G7): daily catalog sync; the 3-day cache
        # inside refresh_catalog makes retries/redundant runs cheap.
        beat_schedule={
            "refresh-model-catalog": {
                "task": "models.refresh_catalog",
                "schedule": 24 * 60 * 60,
            },
            # Task 12 (Plan J, §6): nightly eval fan-out, same interval-seconds
            # style as the entry above (not a crontab).
            "nightly-eval-run": {
                "task": "evals.run_all_workspaces",
                "schedule": 24 * 60 * 60,
            },
            # Task 7 (DOC-9): daily TTL sweep for ephemeral chat attachments,
            # same interval-seconds style as the two entries above.
            "attachment-ttl-cleanup": {
                "task": "attachments.cleanup_stale",
                "schedule": 24 * 60 * 60,
            },
        },
    )
    return app


celery_app = build_celery()
