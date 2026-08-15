"""Task 6: the inbound Discord webhook route
(`POST /external/bots/discord/{webhook_id}`) -- Ed25519 verify FIRST, then
PING/PONG or a deferred-ack + background-task followup for a command
interaction (Discord's interaction ack window is 3 seconds, well under
typical RAG latency -- see api/routes/bots.py::discord_webhook).

The retriever/streamer fakes are hoisted into their own fixtures (rather than
built inline inside discord_client) so 401/verify-failure tests can assert
`retriever.calls == []` and `streamer.calls == []` directly -- proving NO
relay/LLM work ran at all, not merely that the outbound followup wasn't
reached. Starlette BackgroundTasks run to completion before an ASGITransport
httpx client call returns (see api/routes/models.py's background-sync
tests), so the deferred followup send is observable synchronously here."""

import json
import time
from uuid import uuid4

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from ragz.api.app import create_app
from ragz.core.config import Settings, get_settings
from ragz.core.db import build_session_factory
from ragz.modules.auth.models import User
from ragz.modules.bots import service as bots_service
from ragz.modules.documents.models import Document
from ragz.modules.models.models import Model
from ragz.modules.tenancy.models import Workspace, WorkspaceMember
from tests.api.test_bots_telegram import OutboundRecorder
from tests.conftest import FakeRetriever, FakeStreamer, _stub_litellm_handler

# Makes test_bots_telegram.py's `outbound` fixture available here without a
# plain `import ... outbound`, which ruff's pyflakes flags as F811 the
# moment a test function parameter reuses that name (isolation/
# test_api_key_isolation.py's identical precedent for reusing a sibling
# test module's fixture).
pytest_plugins = ["tests.api.test_bots_telegram"]

_PRIVATE_KEY = Ed25519PrivateKey.generate()
DISCORD_PUBLIC_KEY_HEX = _PRIVATE_KEY.public_key().public_bytes_raw().hex()


def _discord_headers(body: bytes, timestamp: str | None = None) -> dict[str, str]:
    # RAGZ-PUB-10: verify_discord now rejects a stale X-Signature-Timestamp,
    # so the default here must be "now" (was a fixed 2023 epoch value, which
    # every existing test in this file implicitly relied on being accepted
    # forever) -- individual tests still pass an explicit stale `timestamp`
    # to exercise that rejection path.
    ts = timestamp if timestamp is not None else str(int(time.time()))
    signature = _PRIVATE_KEY.sign(ts.encode() + body).hex()
    return {"x-signature-ed25519": signature, "x-signature-timestamp": ts}


@pytest.fixture
async def discord_ws(session: AsyncSession, seeded_user: User):
    ws = Workspace(org_id=seeded_user.org_id, name="DiscordWS")
    session.add(ws)
    await session.flush()
    session.add(WorkspaceMember(workspace_id=ws.id, user_id=seeded_user.id))
    model = Model(
        litellm_model_name=f"discord-{ws.id}", display_name="Discord", provider_kind="ollama"
    )
    session.add(model)
    # A real indexed Document that FakeRetriever's chunks point at -- see
    # test_bots_telegram.py's telegram_env fixture for the identical
    # rationale (get_document_checked 404s on an unregistered id).
    doc = Document(
        org_id=seeded_user.org_id, workspace_id=ws.id, filename="report.pdf",
        mime="application/pdf", size_bytes=10, content_hash="h", status="indexed",
        storage_key="k", created_by=seeded_user.id, lineage_id=uuid4(),
    )
    session.add(doc)
    await session.flush()
    ws.default_model_id = model.id
    session.add(ws)
    await session.commit()
    return ws, doc


@pytest.fixture
async def discord_env(session: AsyncSession, seeded_user: User, discord_ws):
    ws, _doc = discord_ws
    integration = await bots_service.create_integration(
        session, Settings(_env_file=None), actor_id=seeded_user.id, platform="discord", name="d",
        workspace_id=ws.id, user_id=seeded_user.id, token="bot-tok",  # noqa: S106
        signing_secret=DISCORD_PUBLIC_KEY_HEX,
    )
    return integration


@pytest.fixture
def retriever(discord_ws) -> FakeRetriever:
    _ws, doc = discord_ws
    return FakeRetriever(doc.id)


@pytest.fixture
def streamer() -> FakeStreamer:
    return FakeStreamer()


@pytest.fixture
async def discord_client(
    engine: AsyncEngine, redis_client, outbound: OutboundRecorder,
    retriever: FakeRetriever, streamer: FakeStreamer,
) -> httpx.AsyncClient:
    app = create_app(
        session_factory=build_session_factory(engine), redis_client=redis_client,
        litellm_transport=httpx.MockTransport(_stub_litellm_handler),
        retriever=retriever, llm_streamer=streamer,
        bot_outbound_transport=outbound.transport,
    )
    app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_ping_returns_pong(discord_client: httpx.AsyncClient, discord_env) -> None:
    body = json.dumps({"type": 1}).encode()
    r = await discord_client.post(
        f"/external/bots/discord/{discord_env.webhook_id}", content=body,
        headers=_discord_headers(body),
    )
    assert r.status_code == 200
    assert r.json() == {"type": 1}


async def test_valid_command_acks_deferred_and_sends_followup(
    discord_client: httpx.AsyncClient, discord_env, outbound: OutboundRecorder
) -> None:
    body = json.dumps(
        {
            "type": 2, "channel_id": "C1", "application_id": "app1", "token": "interaction-tok",
            "data": {
                "name": "ask",
                "options": [{"name": "question", "type": 3, "value": "what was revenue?"}],
            },
        }
    ).encode()
    r = await discord_client.post(
        f"/external/bots/discord/{discord_env.webhook_id}", content=body,
        headers=_discord_headers(body),
    )
    assert r.status_code == 200
    assert r.json() == {"type": 5}
    assert len(outbound.calls) == 1
    assert outbound.calls[0].url.path == "/api/v10/webhooks/app1/interaction-tok/messages/@original"


async def test_replayed_interaction_id_is_ignored_no_second_outbound_call(
    discord_client: httpx.AsyncClient, discord_env, outbound: OutboundRecorder
) -> None:
    """RAGZ-PUB-10: Discord's signature stays valid for the whole freshness
    window verify_discord now enforces -- a replay inside that window must
    still be caught by the interaction `id` dedup claim. The replay still
    acks the same deferred response (so Discord doesn't retry) but triggers
    no second relay/followup."""
    body = json.dumps(
        {
            "type": 2, "id": "interaction-replay-1", "channel_id": "C1",
            "application_id": "app1", "token": "interaction-tok",
            "data": {
                "name": "ask",
                "options": [{"name": "question", "type": 3, "value": "what was revenue?"}],
            },
        }
    ).encode()
    headers = _discord_headers(body)
    r1 = await discord_client.post(
        f"/external/bots/discord/{discord_env.webhook_id}", content=body, headers=headers,
    )
    r2 = await discord_client.post(
        f"/external/bots/discord/{discord_env.webhook_id}", content=body, headers=headers,
    )
    assert r1.status_code == 200, r1.text
    assert r2.status_code == 200, r2.text
    assert r2.json() == {"type": 5}
    assert len(outbound.calls) == 1


async def test_new_interaction_id_after_replay_still_processes(
    discord_client: httpx.AsyncClient, discord_env, outbound: OutboundRecorder
) -> None:
    def _command(interaction_id: str) -> bytes:
        return json.dumps(
            {
                "type": 2, "id": interaction_id, "channel_id": "C1",
                "application_id": "app1", "token": "interaction-tok",
                "data": {
                    "name": "ask",
                    "options": [{"name": "question", "type": 3, "value": "q"}],
                },
            }
        ).encode()

    first = _command("interaction-a")
    second = _command("interaction-b")
    r1 = await discord_client.post(
        f"/external/bots/discord/{discord_env.webhook_id}", content=first,
        headers=_discord_headers(first),
    )
    r2 = await discord_client.post(
        f"/external/bots/discord/{discord_env.webhook_id}", content=second,
        headers=_discord_headers(second),
    )
    assert r1.status_code == 200, r1.text
    assert r2.status_code == 200, r2.text
    assert len(outbound.calls) == 2


async def test_ping_repeats_without_dedup(discord_client: httpx.AsyncClient, discord_env) -> None:
    """PING/PONG (the handshake) carries no interaction dedup concern and
    must keep working every time Discord re-probes it during setup."""
    body = json.dumps({"type": 1}).encode()
    r1 = await discord_client.post(
        f"/external/bots/discord/{discord_env.webhook_id}", content=body,
        headers=_discord_headers(body),
    )
    r2 = await discord_client.post(
        f"/external/bots/discord/{discord_env.webhook_id}", content=body,
        headers=_discord_headers(body),
    )
    assert r1.status_code == 200 and r1.json() == {"type": 1}
    assert r2.status_code == 200 and r2.json() == {"type": 1}


async def test_stale_timestamp_returns_401_with_no_relay_or_outbound(
    discord_client: httpx.AsyncClient, discord_env, outbound: OutboundRecorder,
    retriever: FakeRetriever, streamer: FakeStreamer,
) -> None:
    body = json.dumps({"type": 1}).encode()
    stale_ts = str(int(time.time()) - 400)  # > 5 min old
    r = await discord_client.post(
        f"/external/bots/discord/{discord_env.webhook_id}", content=body,
        headers=_discord_headers(body, timestamp=stale_ts),
    )
    assert r.status_code == 401
    assert retriever.calls == []
    assert streamer.calls == []
    assert outbound.calls == []


async def test_failed_signature_does_not_claim_dedup_key(
    discord_client: httpx.AsyncClient, discord_env, outbound: OutboundRecorder
) -> None:
    body = json.dumps(
        {
            "type": 2, "id": "interaction-badsig", "channel_id": "C1",
            "application_id": "app1", "token": "interaction-tok",
            "data": {
                "name": "ask",
                "options": [{"name": "question", "type": 3, "value": "q"}],
            },
        }
    ).encode()
    bad_headers = _discord_headers(body)
    bad_headers["x-signature-ed25519"] = "00" * 64
    bad = await discord_client.post(
        f"/external/bots/discord/{discord_env.webhook_id}", content=body, headers=bad_headers,
    )
    assert bad.status_code == 401
    good = await discord_client.post(
        f"/external/bots/discord/{discord_env.webhook_id}", content=body,
        headers=_discord_headers(body),
    )
    assert good.status_code == 200, good.text
    assert len(outbound.calls) == 1  # the earlier 401 never claimed the interaction id


async def test_tampered_signature_returns_401_with_no_relay_or_outbound(
    discord_client: httpx.AsyncClient, discord_env, outbound: OutboundRecorder,
    retriever: FakeRetriever, streamer: FakeStreamer,
) -> None:
    body = json.dumps({"type": 1}).encode()
    headers = _discord_headers(body)
    headers["x-signature-ed25519"] = "00" * 64  # syntactically valid hex, wrong signature
    r = await discord_client.post(
        f"/external/bots/discord/{discord_env.webhook_id}", content=body, headers=headers,
    )
    assert r.status_code == 401
    assert retriever.calls == []
    assert streamer.calls == []
    assert outbound.calls == []


async def test_unknown_webhook_id_returns_404(discord_client: httpx.AsyncClient) -> None:
    body = json.dumps({"type": 1}).encode()
    r = await discord_client.post(
        f"/external/bots/discord/{uuid4()}", content=body, headers=_discord_headers(body),
    )
    assert r.status_code == 404
