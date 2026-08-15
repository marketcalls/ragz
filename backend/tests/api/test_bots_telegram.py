"""Task 5: the inbound Telegram webhook route
(`POST /external/bots/telegram/{webhook_id}`) -- the first inbound
chat-platform route (design doc §Components item 6). Exercises the full
verify -> parse -> relay -> outbound chain end to end, with the iron-rule
assertion that a bad/absent signature does NO relay/LLM/outbound work at
all (proven here by asserting the outbound MockTransport was never hit)."""

import json
from collections.abc import AsyncIterator
from uuid import uuid4

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from ragz.api.app import create_app
from ragz.api.routes.bots import _WEBHOOK_BODY_MAX_BYTES
from ragz.core.config import Settings, get_settings
from ragz.core.db import build_session_factory
from ragz.modules.auth.models import User
from ragz.modules.bots import service as bots_service
from ragz.modules.documents.models import Document
from ragz.modules.models.models import Model
from ragz.modules.tenancy.models import Workspace, WorkspaceMember
from tests.conftest import FakeRetriever, FakeStreamer, _stub_litellm_handler

TELEGRAM_SECRET = "tg-webhook-secret-value"  # noqa: S105 -- test fixture value, not a real secret


@pytest.fixture
async def telegram_env(session: AsyncSession, seeded_user: User):
    ws = Workspace(org_id=seeded_user.org_id, name="TgWS")
    session.add(ws)
    await session.flush()
    session.add(WorkspaceMember(workspace_id=ws.id, user_id=seeded_user.id))
    model = Model(litellm_model_name=f"tg-{ws.id}", display_name="Tg", provider_kind="ollama")
    session.add(model)
    # A real indexed Document (relay_env's pattern, test_bots_relay.py) that
    # FakeRetriever's chunks point at -- stream_reply's _prepare_sources
    # resolves each chunk's document_id via
    # documents.service.get_document_checked, which 404s on an unregistered
    # id and fails the whole answer with a 502 rather than exercising the
    # route -- so this needs to be a real row, not a bare uuid4().
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
    integration = await bots_service.create_integration(
        session, Settings(_env_file=None), actor_id=seeded_user.id, platform="telegram", name="t",
        workspace_id=ws.id, user_id=seeded_user.id, token="tg-token",  # noqa: S106
        signing_secret=TELEGRAM_SECRET,
    )
    return integration, doc


class OutboundRecorder:
    def __init__(self) -> None:
        self.calls: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        return httpx.Response(200, json={"ok": True})

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)


@pytest.fixture
def outbound() -> OutboundRecorder:
    return OutboundRecorder()


@pytest.fixture
def retriever(telegram_env) -> FakeRetriever:
    # Hoisted (rather than built inline in telegram_client) so 401 tests can
    # assert `retriever.calls == []` directly -- proving NO relay/LLM work
    # ran at all, not merely that the outbound send wasn't reached.
    _integration, doc = telegram_env
    return FakeRetriever(doc.id)


@pytest.fixture
def streamer() -> FakeStreamer:
    return FakeStreamer()


@pytest.fixture
async def telegram_client(
    engine: AsyncEngine, redis_client, outbound: OutboundRecorder,
    retriever: FakeRetriever, streamer: FakeStreamer,
) -> httpx.AsyncClient:
    app = create_app(
        session_factory=build_session_factory(engine),
        redis_client=redis_client,
        litellm_transport=httpx.MockTransport(_stub_litellm_handler),
        retriever=retriever,
        llm_streamer=streamer,
        bot_outbound_transport=outbound.transport,
    )
    app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _update_body(text: str) -> bytes:
    return json.dumps({"update_id": 1, "message": {"chat": {"id": 555}, "text": text}}).encode()


async def test_valid_signature_returns_200_and_calls_outbound(
    telegram_client: httpx.AsyncClient, telegram_env, outbound: OutboundRecorder
) -> None:
    integration, _doc = telegram_env
    body = _update_body("what was revenue?")
    r = await telegram_client.post(
        f"/external/bots/telegram/{integration.webhook_id}",
        content=body,
        headers={
            "X-Telegram-Bot-Api-Secret-Token": TELEGRAM_SECRET,
            "content-type": "application/json",
        },
    )
    assert r.status_code == 200, r.text
    assert len(outbound.calls) == 1
    assert outbound.calls[0].url.path == "/bottg-token/sendMessage"
    sent = json.loads(outbound.calls[0].content)
    assert sent["chat_id"] == "555"
    assert sent["text"]  # the RAG answer


async def test_tampered_signature_returns_401_with_no_outbound_or_llm_call(
    telegram_client: httpx.AsyncClient, telegram_env, outbound: OutboundRecorder,
    retriever: FakeRetriever, streamer: FakeStreamer,
) -> None:
    integration, _doc = telegram_env
    r = await telegram_client.post(
        f"/external/bots/telegram/{integration.webhook_id}",
        content=_update_body("anything"),
        headers={
            "X-Telegram-Bot-Api-Secret-Token": "wrong-secret",
            "content-type": "application/json",
        },
    )
    assert r.status_code == 401
    assert retriever.calls == []
    assert streamer.calls == []
    assert outbound.calls == []


async def test_missing_signature_returns_401(
    telegram_client: httpx.AsyncClient, telegram_env, outbound: OutboundRecorder,
    retriever: FakeRetriever, streamer: FakeStreamer,
) -> None:
    integration, _doc = telegram_env
    r = await telegram_client.post(
        f"/external/bots/telegram/{integration.webhook_id}", content=_update_body("x"),
    )
    assert r.status_code == 401
    assert retriever.calls == []
    assert streamer.calls == []
    assert outbound.calls == []


async def test_unknown_webhook_id_returns_404(telegram_client: httpx.AsyncClient) -> None:
    r = await telegram_client.post(
        f"/external/bots/telegram/{uuid4()}", content=_update_body("x"),
        headers={"X-Telegram-Bot-Api-Secret-Token": "whatever"},
    )
    assert r.status_code == 404


async def test_disabled_integration_returns_404(
    telegram_client: httpx.AsyncClient, telegram_env, session: AsyncSession
) -> None:
    integration, _doc = telegram_env
    await bots_service.set_enabled(session, integration_id=integration.id, enabled=False)
    r = await telegram_client.post(
        f"/external/bots/telegram/{integration.webhook_id}",
        content=_update_body("x"),
        headers={"X-Telegram-Bot-Api-Secret-Token": TELEGRAM_SECRET},
    )
    assert r.status_code == 404


async def test_non_message_update_returns_200_no_op(
    telegram_client: httpx.AsyncClient, telegram_env, outbound: OutboundRecorder
) -> None:
    integration, _doc = telegram_env
    body = json.dumps({"update_id": 2, "edited_message": {"chat": {"id": 555}}}).encode()
    r = await telegram_client.post(
        f"/external/bots/telegram/{integration.webhook_id}",
        content=body,
        headers={
            "X-Telegram-Bot-Api-Secret-Token": TELEGRAM_SECRET,
            "content-type": "application/json",
        },
    )
    assert r.status_code == 200, r.text
    assert outbound.calls == []


async def test_oversized_body_rejected_413_before_signature_check(
    telegram_client: httpx.AsyncClient, telegram_env, outbound: OutboundRecorder,
    retriever: FakeRetriever, streamer: FakeStreamer,
) -> None:
    """RAGZ-PUB-03: the webhook byte cap is enforced BEFORE signature
    verification/parse/relay -- proven here with a deliberately WRONG
    secret: if the cap weren't checked first, this would 401 (wrong
    signature) rather than 413 (body too large), and the relay/LLM/outbound
    assertions below would still hold either way (iron rule: no work below
    an unverified/oversized request)."""
    integration, _doc = telegram_env
    oversized = _update_body("x" * (_WEBHOOK_BODY_MAX_BYTES + 1))
    r = await telegram_client.post(
        f"/external/bots/telegram/{integration.webhook_id}",
        content=oversized,
        headers={
            "X-Telegram-Bot-Api-Secret-Token": "wrong-secret",
            "content-type": "application/json",
        },
    )
    assert r.status_code == 413, r.text
    assert retriever.calls == []
    assert streamer.calls == []
    assert outbound.calls == []


async def test_oversized_chunked_body_without_content_length_rejected_413(
    telegram_client: httpx.AsyncClient, telegram_env, outbound: OutboundRecorder,
    retriever: FakeRetriever, streamer: FakeStreamer,
) -> None:
    """RAGZ-PUB-03 review: the streaming cap must fire even with NO
    Content-Length header (chunked transfer). A generator body makes httpx
    omit Content-Length, so the declared-length short-circuit is skipped and
    only the incremental request.stream() accumulation can stop it -- the
    exact path the earlier request.body() version left buffering to the
    global ~25-45MB ceiling. Still 413s before any relay/LLM/outbound work."""
    integration, _doc = telegram_env

    async def _oversized_chunks() -> AsyncIterator[bytes]:
        # ~256KB total (4x the 64KB cap) streamed in 8KB chunks, no Content-Length.
        for _ in range((_WEBHOOK_BODY_MAX_BYTES * 4) // (8 * 1024)):
            yield b"x" * (8 * 1024)

    r = await telegram_client.post(
        f"/external/bots/telegram/{integration.webhook_id}",
        content=_oversized_chunks(),
        headers={
            "X-Telegram-Bot-Api-Secret-Token": TELEGRAM_SECRET,
            "content-type": "application/json",
        },
    )
    assert "content-length" not in r.request.headers  # truly chunked
    assert r.status_code == 413, r.text
    assert retriever.calls == []
    assert streamer.calls == []
    assert outbound.calls == []


async def test_body_at_cap_is_accepted(
    telegram_client: httpx.AsyncClient, telegram_env, outbound: OutboundRecorder
) -> None:
    """A body comfortably under the cap is unaffected -- still verifies and
    relays normally (mirrors test_valid_signature_returns_200_and_calls_
    outbound, just with a large-but-in-bounds text field)."""
    integration, _doc = telegram_env
    # Leaves headroom under _WEBHOOK_BODY_MAX_BYTES for the update's JSON
    # scaffolding (update_id/chat/id keys) added by _update_body.
    body = _update_body("what was revenue? " + "x" * (_WEBHOOK_BODY_MAX_BYTES // 2))
    assert len(body) <= _WEBHOOK_BODY_MAX_BYTES
    r = await telegram_client.post(
        f"/external/bots/telegram/{integration.webhook_id}",
        content=body,
        headers={
            "X-Telegram-Bot-Api-Secret-Token": TELEGRAM_SECRET,
            "content-type": "application/json",
        },
    )
    assert r.status_code == 200, r.text
    assert len(outbound.calls) == 1
