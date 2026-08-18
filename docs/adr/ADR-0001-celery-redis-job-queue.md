# ADR-0001: Celery + Redis for the Job Queue

**Date:** 2026-07-18
**Status:** Accepted

## Context

Document ingestion (parse → chunk → embed → upsert) is long-running, must scale horizontally, and needs retries with visibility. The PRD's risk register requires small interactive uploads to preempt bulk-load backlogs, which demands priority queues. Candidates: Celery, arq, Dramatiq.

## Decision

Celery with Redis as broker. Task entrypoints live in `backend/src/ragz/worker/` and are thin wrappers over module services.

## Rationale

- Priority-queue support out of the box (arq lacks it; Dramatiq's is coarser).
- Mature retry, backoff, and visibility semantics; broad operational knowledge among self-hosting admins.
- Horizontal worker scaling matches the PRD's ingestion throughput target (≥50 pages/sec per worker, scaled by adding workers).

## Consequences

- Celery tasks are sync-first; async module code is invoked via an event loop inside tasks — an accepted seam. **Superseded in part by [ADR-0006](ADR-0006-worker-event-loop-per-process.md):** the `asyncio.run`-per-task half of this was replaced by one long-lived loop per worker process, because a per-task loop forced a per-task connection pool.
- Redis becomes a required service (already needed for caching/quota counters).
- Revisitable if Celery's asyncio story becomes a real constraint; the thin `worker/` entrypoint layer keeps a queue swap localized.
