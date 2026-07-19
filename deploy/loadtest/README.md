# RagHub load-test harness (D2)

## One-time setup
1. `docker compose -f deploy/compose.yaml up -d` (postgres, redis, litellm required; ml profile NOT needed — the mock model answers without a provider)
2. `cd backend && uv run alembic upgrade head`
3. `uv run python ../deploy/loadtest/seed.py`
4. Start the app: `uv run uvicorn --factory raghub.api.app:create_app --port 8000`
5. Start a worker (only needed for the upload scenario):
   `uv run celery -A raghub.worker.celery_app:celery_app worker -Q interactive,default -l warning`

## Acceptance run (100 concurrent SSE chats, D2 gate)
    cd backend && uv sync --group loadtest
    uv run --group loadtest locust -f ../deploy/loadtest/locustfile.py \
      --headless -u 100 -r 10 -t 2m --host http://localhost:8000 --only-summary

PASS = 0 failures on `/chats/[id]/messages [SSE]` and p95 total stream time < 5s
on dev hardware. Record the summary table in the PR description.

Note: seeded users have NO quota rows -> unlimited (Plan F semantics), so the
run exercises the pre-flight quota check + its 60s Redis cache without ever
blocking. Watch the admin usage summary latency during the run: if it degrades,
that is the signal Plan F deferred to us for materializing rollups (contract C1).

**Known limitation — login rate limit.** `/api/v1/auth/login` is rate-limited
per client IP at 10 requests/60s (Plan A, `core/ratelimit.py`), not per
account. A headless locust run drives every simulated user's `on_start` login
from the single IP of the test box, so ramping faster than 10 accounts/60s
will 429 some logins. The `-r 10` ramp above (10 new users/sec) will hit this
in the first couple of seconds of the acceptance run. Before running the full
100-user acceptance pass, either: raise the login rate limit for the loadtest
source IP, add a `RAGHUB_` env override for a higher limit in the dev/loadtest
profile, or slow the ramp to `-r 1` (10x slower spin-up, but each account
logs in once so it clears the window). This is not fixed by this harness —
tracked here so Task 16 (the full acceptance run) does not silently record
false failures as SSE/chat problems.

## Baseline procedure
Run the acceptance command 3x after a 30s warm run; keep the middle result.
Re-run after any change to pool sizes (RAGHUB_DB_POOL_SIZE etc.), worker
counts, or the chat hot path, and compare p50/p95/rps. The retrieval-path
scenario requires indexed documents + the `ml` compose profile and is a
manual variation (change the message content to a real question).
