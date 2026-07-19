"""N concurrent SSE chats + uploads against a running RagHub backend.

Each simulated user logs in as its own seeded account (per-user chat rate
limit is 30/60s - one account per locust user stays far under it).

Deviation from the D2 brief: seeded emails use ``@loadtest-raghub.com``
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

Known limitation NOT handled here (flagged, not fixed - out of this task's
scope): ``/api/v1/auth/login`` is rate-limited per client IP at 10/60s
(``core/ratelimit.py``), not per account. A headless locust run driving many
simulated users from one test box shares that single IP, so any ramp that
logs in faster than 10 accounts/60s will 429 on `on_start`. The smoke run in
the README (10 users, ramp 5/s) stays exactly at that boundary; the 100-user
acceptance run (Task 16) will need either a slower ramp, per-IP allowlisting
for the loadtest source, or a rate-limit carve-out - none of which this task
implements.

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

from locust import HttpUser, between, task

_counter = itertools.count()
_upload_counter = itertools.count()
EMAIL_DOMAIN = "loadtest-raghub.com"


class ChatUser(HttpUser):
    wait_time = between(1, 3)

    def on_start(self) -> None:
        n = next(_counter) % 100
        r = self.client.post("/api/v1/auth/login", json={
            "email": f"lt-{n:03d}@{EMAIL_DOMAIN}", "password": "loadtest-pw-1",
        })
        r.raise_for_status()
        self.headers = {"Authorization": f"Bearer {r.json()['access_token']}"}
        ws = self.client.get("/api/v1/workspaces", headers=self.headers).json()
        self.workspace_id = ws[0]["id"]

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
