"""Task 6: the inbound Slack webhook route
(`POST /external/bots/slack/{webhook_id}`) -- verify -> handshake/parse ->
relay -> outbound, mirroring test_bots_telegram.py's shape. Slack's
url_verification handshake still requires signature verification FIRST (iron
rule: no work -- not even echoing a challenge -- before a valid signature),
and a stale timestamp is rejected as a replay guard.

The retriever/streamer fakes are hoisted into their own fixtures (rather than
built inline inside slack_client) so 401/verify-failure tests can assert
`retriever.calls == []` and `streamer.calls == []` directly -- proving NO
relay/LLM work ran at all, not merely that the outbound send wasn't reached."""

import hashlib
import hmac
import json
import time
from uuid import uuid4

import httpx
import pytest
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

SLACK_SECRET = "slack-signing-secret-value"  # noqa: S105 -- test fixture value, not a real secret


def _slack_headers(body: bytes, timestamp: int | None = None) -> dict[str, str]:
    ts = timestamp if timestamp is not None else int(time.time())
    basestring = f"v0:{ts}:".encode() + body
    sig = "v0=" + hmac.new(SLACK_SECRET.encode(), basestring, hashlib.sha256).hexdigest()
    return {"x-slack-request-timestamp": str(ts), "x-slack-signature": sig}


@pytest.fixture
async def slack_ws(session: AsyncSession, seeded_user: User):
    ws = Workspace(org_id=seeded_user.org_id, name="SlackWS")
    session.add(ws)
    await session.flush()
    session.add(WorkspaceMember(workspace_id=ws.id, user_id=seeded_user.id))
    model = Model(
        litellm_model_name=f"slack-{ws.id}", display_name="Slack", provider_kind="ollama"
    )
    session.add(model)
    # A real indexed Document that FakeRetriever's chunks point at --
    # stream_reply's _prepare_sources resolves each chunk's document_id via
    # documents.service.get_document_checked, which 404s on an unregistered
    # id (telegram_env's identical rationale, test_bots_telegram.py) -- so
    # this needs to be a real row, not a bare uuid4().
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
async def slack_env(session: AsyncSession, seeded_user: User, slack_ws):
    ws, _doc = slack_ws
    integration = await bots_service.create_integration(
        session, Settings(_env_file=None), actor_id=seeded_user.id, platform="slack", name="s",
        workspace_id=ws.id, user_id=seeded_user.id, token="xoxb-tok", signing_secret=SLACK_SECRET,  # noqa: S106
    )
    return integration


@pytest.fixture
def retriever(slack_ws) -> FakeRetriever:
    _ws, doc = slack_ws
    return FakeRetriever(doc.id)


@pytest.fixture
def streamer() -> FakeStreamer:
    return FakeStreamer()


@pytest.fixture
async def slack_client(
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


async def test_url_verification_echoes_challenge(
    slack_client: httpx.AsyncClient, slack_env
) -> None:
    body = json.dumps({"type": "url_verification", "challenge": "abc123"}).encode()
    r = await slack_client.post(
        f"/external/bots/slack/{slack_env.webhook_id}", content=body, headers=_slack_headers(body),
    )
    assert r.status_code == 200
    assert r.json() == {"challenge": "abc123"}


async def test_valid_message_event_acks_and_calls_outbound_via_background_task(
    slack_client: httpx.AsyncClient, slack_env, outbound: OutboundRecorder
) -> None:
    body = json.dumps(
        {
            "type": "event_callback",
            "event": {"type": "message", "channel": "C1", "text": "hi there"},
        }
    ).encode()
    r = await slack_client.post(
        f"/external/bots/slack/{slack_env.webhook_id}", content=body, headers=_slack_headers(body),
    )
    assert r.status_code == 200
    assert len(outbound.calls) == 1
    assert outbound.calls[0].url.path == "/api/chat.postMessage"
    sent = json.loads(outbound.calls[0].content)
    assert sent["channel"] == "C1"


async def test_tampered_signature_returns_401_with_no_relay_or_outbound(
    slack_client: httpx.AsyncClient, slack_env, outbound: OutboundRecorder,
    retriever: FakeRetriever, streamer: FakeStreamer,
) -> None:
    body = json.dumps(
        {"type": "event_callback", "event": {"type": "message", "channel": "C1", "text": "hi"}}
    ).encode()
    headers = _slack_headers(body)
    headers["x-slack-signature"] = "v0=deadbeef"
    r = await slack_client.post(
        f"/external/bots/slack/{slack_env.webhook_id}", content=body, headers=headers,
    )
    assert r.status_code == 401
    assert retriever.calls == []
    assert streamer.calls == []
    assert outbound.calls == []


async def test_stale_timestamp_returns_401(
    slack_client: httpx.AsyncClient, slack_env, outbound: OutboundRecorder,
    retriever: FakeRetriever, streamer: FakeStreamer,
) -> None:
    body = json.dumps({"type": "url_verification", "challenge": "x"}).encode()
    headers = _slack_headers(body, timestamp=int(time.time()) - 400)
    r = await slack_client.post(
        f"/external/bots/slack/{slack_env.webhook_id}", content=body, headers=headers,
    )
    assert r.status_code == 401
    assert retriever.calls == []
    assert streamer.calls == []
    assert outbound.calls == []


async def test_unknown_webhook_id_returns_404(slack_client: httpx.AsyncClient) -> None:
    body = json.dumps({"type": "url_verification", "challenge": "x"}).encode()
    r = await slack_client.post(
        f"/external/bots/slack/{uuid4()}", content=body, headers=_slack_headers(body),
    )
    assert r.status_code == 404
