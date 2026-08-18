# Ragz load-test harness (D2)

## One-time setup
1. `docker compose -f deploy/compose.yaml up -d` (postgres, redis, litellm required; ml profile NOT needed — the mock model answers without a provider)
2. `cd backend && uv run alembic upgrade head`
3. `uv run python ../deploy/loadtest/seed.py` — seeds the tenant/users AND mints
   one long-lived access token per user into `deploy/loadtest/.tokens.json`
   (gitignored; re-run any time to refresh it, e.g. before each acceptance run).
4. Start the app: `uv run uvicorn --factory ragz.api.app:create_app --port 8000`
5. Start a worker (only needed for the upload scenario):
   `uv run celery -A ragz.worker.celery_app:celery_app worker -Q interactive,default,maintenance -l warning`

## Acceptance run (100 concurrent SSE chats, D2 gate)
Procedure: seed (writes `.tokens.json`) → locust.

    cd backend
    uv run python ../deploy/loadtest/seed.py
    uv sync --group loadtest
    uv run --group loadtest locust -f ../deploy/loadtest/locustfile.py \
      --headless -u 100 -r 10 -t 2m --host http://localhost:8000 --only-summary

PASS = 0 failures on `/chats/[id]/messages [SSE]` and p95 total stream time < 5s
on dev hardware, **and** the run does not print the `FAILED: only N/100 simulated
users completed on_start` banner (see below) — that banner means the run was not
actually at the concurrency it claims, regardless of what the SSE stats show.
Record the summary table in the PR description.

Note: seeded users have NO quota rows -> unlimited (Plan F semantics), so the
run exercises the pre-flight quota check + its 60s Redis cache without ever
blocking. Watch the admin usage summary latency during the run: if it degrades,
that is the signal Plan F deferred to us for materializing rollups (contract C1).

**Login rate limit — resolved by default.** `/api/v1/auth/login` is
rate-limited per client IP at 10 requests/60s (Plan A, `core/ratelimit.py`),
not per account, and a headless run driving 100 simulated users' `on_start`
from one test box IP would 429 ~90 of them — silently, since the PASS
criteria above only look at the SSE endpoint. `seed.py` now mints tokens
directly (bypassing the rate-limited route) and `locustfile.py`'s `on_start`
reads them from `.tokens.json` by default, so this no longer applies to a
normal acceptance run. It only resurfaces if you skip the seed step or the
token file is stale/incomplete: `on_start` falls back to a live login per
missing token, capped at the endpoint's own 10/60s budget — past that it
raises loudly instead of letting the rest 429 silently. If you hit that
error, re-run `seed.py`; the fallback ramp (`-r 1`, one new live login per
second) is a last-resort workaround, not the default path.

## Baseline procedure
Run the acceptance command 3x after a 30s warm run; keep the middle result.
Re-run after any change to pool sizes (RAGZ_DB_POOL_SIZE etc.), worker
counts, or the chat hot path, and compare p50/p95/rps. The retrieval-path
scenario requires indexed documents + the `ml` compose profile and is a
manual variation (change the message content to a real question).
