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


def _discord_headers(body: bytes, timestamp: str = "1700000000") -> dict[str, str]:
    signature = _PRIVATE_KEY.sign(timestamp.encode() + body).hex()
    return {"x-signature-ed25519": signature, "x-signature-timestamp": timestamp}


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
