"""N concurrent SSE chats + uploads against a running Ragz backend.

Each simulated user logs in as its own seeded account (per-user chat rate
limit is 30/60s - one account per locust user stays far under it).

Deviation from the D2 brief: seeded emails use ``@loadtest-ragz.com``
rather than ``@loadtest.local`` (see ``deploy/loadtest/seed.py`` docstring -
``EmailStr`` rejects the ``.local`` special-use TLD, which would 422 every
login). The upload path is also adapted to the real route
(``/api/v1/workspaces/{workspace_id}/documents``, a path param - not a
top-level ``/api/v1/documents`` with a query param).

Second deviation, verified by hand against a running stack: the brief's
message body was ``"hello there"`` with a comment claiming it hits the
"conversational route -> mock model, no retrieval". It does not.
``classify_query`` (``modules/chat/router.py``) only treats a message as
conversational if the ENTIRE normalized string is a bare greeting/closer
("hi", "hello", "thanks", ...) - "hello there" falls through to the
retrieval path, and with no documents indexed for this workspace that path
always terminates in the no-answer gate (`no_answer: true`) without ever
calling the LLM/mock model. The SSE stream still ends in a `done` frame
either way, so the locust check alone would not have caught this - only
sending the raw request and reading the event stream did. Using a bare
"hello" instead correctly takes the conversational branch and streams the
seeded model's canned `mock_response` text through the real LiteLLM proxy
hop, which is the entire point of D2.

Review round 1 fix - login rate limit: ``/api/v1/auth/login`` is rate-limited
per client IP at 10/60s (``core/ratelimit.py``), not per account. A headless
locust run driving 100 simulated users' `on_start` from one test box IP
would 429 ~90 of them, and the README's PASS criteria only looked at the SSE
endpoint - so that silently reported a clean pass at 10x lower concurrency
than the NFR it claims to validate. Fixed by making pre-provisioned tokens
the default path: ``deploy/loadtest/seed.py`` now mints one long-lived
access token per seeded user (bypassing the rate-limited login route
entirely - see its docstring) and writes them to ``.tokens.json``.
``on_start`` below reads its user's token from that file and only falls
back to a live ``/auth/login`` call - loudly capped at the endpoint's own
10/60s budget - when the file is missing or a user's slot isn't in it.

Third deviation, also found by running the smoke test rather than reading
code: the brief's upload task posts the exact same fixed byte string every
time. `documents/service.create_from_upload` dedups by sha256 of the raw
bytes *within a workspace* and 409s on a repeat - harmless in isolation, but
every locust user shares the one seeded workspace, so only the very first
upload of the run ever succeeds; every other one legitimately 409s. That
inflated the smoke run's failure count without indicating any load problem.
Each upload body is now suffixed with a unique counter so the content hash
never collides.
"""

import io
import itertools
import json
import sys
from pathlib import Path

from locust import HttpUser, between, events, task

_counter = itertools.count()
_upload_counter = itertools.count()
EMAIL_DOMAIN = "loadtest-ragz.com"

# /api/v1/auth/login is rate-limited per client IP at 10/60s (core/ratelimit.py).
# If .tokens.json (written by seed.py) is missing or incomplete, on_start falls
# back to a live login - but only up to this budget. Past it, every further
# live login would just 429 anyway, so fail the whole run loudly instead of
# quietly bleeding ~90% of "concurrent" users at higher -u counts.
_LIVE_LOGIN_BUDGET = 10

_TOKENS_PATH = Path(__file__).with_name(".tokens.json")
_tokens: dict[str, str] = {}
if _TOKENS_PATH.exists():
    _tokens = json.loads(_TOKENS_PATH.read_text())["tokens"]

# Hard signal for the "silent false pass" failure mode: count users that
# actually finished on_start (token in hand, workspace resolved) against
# users that were spawned at all. Checked in the `quitting` handler below.
_spawned = 0
_users_ready = 0
_live_login_count = 0


class ChatUser(HttpUser):
    wait_time = between(1, 3)

    def on_start(self) -> None:
        global _spawned, _users_ready, _live_login_count
        _spawned += 1
        n = next(_counter) % 100
        token = _tokens.get(str(n))
        if token is None:
            _live_login_count += 1
            if _live_login_count > _LIVE_LOGIN_BUDGET:
                raise RuntimeError(
                    f"on_start for simulated user {n}: no pre-provisioned token in "
                    f"{_TOKENS_PATH} and {_live_login_count} live /auth/login calls "
                    f"have already been attempted this run, past the endpoint's "
                    f"{_LIVE_LOGIN_BUDGET}/60s per-IP rate limit (core/ratelimit.py). "
                    "Every further live login will just 429. Run "
                    "`uv run python ../deploy/loadtest/seed.py` (from backend/) first "
                    "so on_start reads pre-provisioned tokens instead of logging in live."
                )
            r = self.client.post("/api/v1/auth/login", json={
                "email": f"lt-{n:03d}@{EMAIL_DOMAIN}", "password": "loadtest-pw-1",
            })
            r.raise_for_status()
            token = r.json()["access_token"]
        self.headers = {"Authorization": f"Bearer {token}"}
        ws = self.client.get("/api/v1/workspaces", headers=self.headers).json()
        self.workspace_id = ws[0]["id"]
        _users_ready += 1

    @task(8)
    def chat_sse(self) -> None:
        chat = self.client.post(
            "/api/v1/chats", json={"workspace_id": self.workspace_id},
            headers=self.headers, name="/chats [create]",
        ).json()
        with self.client.post(
            f"/api/v1/chats/{chat['id']}/messages",
            json={"content": "hello"},  # conversational route -> mock model, no retrieval
            headers=self.headers, stream=True, catch_response=True,
            name="/chats/[id]/messages [SSE]",
        ) as resp:
            done = error = False
            for line in resp.iter_lines():
                if line.startswith(b"event: done"):
                    done = True
                elif line.startswith(b"event: error"):
                    error = True
            if done and not error:
                resp.success()
            else:
                resp.failure("stream ended without done frame" if not error else "error frame")

    @task(1)
    def upload(self) -> None:
        # Unique per request: content-hash dedup (documents/service.py) 409s a
        # byte-identical repeat within the same workspace, and every locust
        # user shares the one seeded workspace.
        nonce = next(_upload_counter)
        payload = io.BytesIO(f"loadtest document body {nonce}\n".encode() * 50)
        self.client.post(
            f"/api/v1/workspaces/{self.workspace_id}/documents",
            files={"file": ("loadtest.txt", payload, "text/plain")},
            headers=self.headers, name="/workspaces/[id]/documents [upload]",
        )


@events.quitting.add_listener
def _fail_if_users_never_got_ready(environment, **kwargs) -> None:
    """Loud, headless-exit-code-visible version of the finding this fixes:
    a clean-looking summary on the SSE endpoint alone does not mean the run
    was actually at the concurrency it claims. If fewer users finished
    on_start than were spawned, something (rate limit, missing tokens,
    workspace lookup) silently thinned the run - fail it."""
    if _users_ready < _spawned:
        missing = _spawned - _users_ready
        print(
            f"\n{'=' * 70}\n"
            f"FAILED: only {_users_ready}/{_spawned} simulated users completed "
            f"on_start ({missing} never became ready). The run summary above does "
            "NOT reflect the intended concurrency.\n"
            f"{'=' * 70}\n",
            file=sys.stderr,
        )
        environment.process_exit_code = 1
