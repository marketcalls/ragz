# ADR-0006: One Event Loop per Worker Process

**Date:** 2026-08-18
**Status:** Accepted

## Context

Phase 2 item 4 of the 2026-08-17 architecture review asks for process-lifetime
DB/HTTP/Redis/Qdrant/S3 clients in the API and the worker.

The API already satisfies this: `create_app` builds the engine and Redis client
once, and `get_qdrant()` is `@lru_cache`d, so all three live for the process.

The worker does not, and cannot as currently structured. `ingest._session()` and
seven call sites in `worker/tasks.py` each call `build_engine(...)` and
`await engine.dispose()` around a single task. That includes
`outbox.dispatch_pending`, which runs every 30 seconds from beat *and* on every
API nudge, so the busiest maintenance path in the system builds and tears down a
connection pool each time.

This is not an oversight. ADR-0001 accepted `asyncio.run` per task, which gives
every task a **new event loop**, and asyncpg connections are bound to the loop
that created them. Caching the engine across tasks fails immediately and loudly:

    loop-1: ok (1)
    loop-2: FAILED RuntimeError: Event loop is closed

(measured directly against this codebase's `build_engine`, reusing one engine
across two `asyncio.run()` calls). The per-task `dispose()` is therefore
load-bearing — it is what makes the current model correct.

Not every client shares the constraint. The same experiment against the
`@lru_cache`d `AsyncQdrantClient` succeeded across two loops, so Qdrant reuse in
the worker is safe today and is not evidence that the engine would be.

ADR-0001 anticipated this exact juncture: its Consequences name "an
event-loop-per-worker pattern" as the alternative to `asyncio.run`, and mark the
decision "revisitable if Celery's asyncio story becomes a real constraint."

## Decision

Give each Celery worker process one
long-lived event loop, created on `worker_process_init` and reused by every
task, so a single engine (and any other loop-bound client) can live for the
process. `_run` would submit coroutines to that loop instead of calling
`asyncio.run`.

## Rationale

- It is the only way to get item 4's benefit for the DB. Every alternative
  (caching the engine, NullPool, per-task disposal) either breaks on the
  loop binding or keeps the connection churn the item exists to remove.
- Connection churn is per-task today: one TCP connect, TLS and auth round trip
  for every parse, chunk, embed, delete, reindex and every 30-second outbox
  sweep. Under load that is a constant tax and a source of Postgres connection
  pressure that no pool sizing can fix.
- The blast radius is confined to `worker/`, which ADR-0001 deliberately kept
  thin for this reason.

## Consequences

- Supersedes the `asyncio.run`-per-task half of ADR-0001's Consequences.
  ADR-0001's substantive decision (Celery + Redis) is unaffected.
- Every task changes execution context at once. A loop shared across tasks
  turns a leaked task or un-awaited coroutine from a per-task annoyance into a
  process-lifetime leak, so this needs its own tests, not just the existing
  suite passing.
- `--pool=solo` (the documented macOS path) and prefork behave differently
  here; both need verifying before this is accepted.
- Until it is accepted, the per-task `build_engine`/`dispose` pattern stays.
  It is correct, just wasteful, and it must not be "optimised" by caching the
  engine — that is the failure reproduced above.
