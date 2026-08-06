"""Task 5: the inbound Telegram webhook route
(`POST /external/bots/telegram/{webhook_id}`) -- the first inbound
chat-platform route (design doc §Components item 6). Exercises the full
verify -> parse -> relay -> outbound chain end to end, with the iron-rule
assertion that a bad/absent signature does NO relay/LLM/outbound work at
all (proven here by asserting the outbound MockTransport was never hit)."""

import json
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
async def telegram_client(
    engine: AsyncEngine, redis_client, outbound: OutboundRecorder, telegram_env
) -> httpx.AsyncClient:
    _integration, doc = telegram_env
    app = create_app(
        session_factory=build_session_factory(engine),
        redis_client=redis_client,
        litellm_transport=httpx.MockTransport(_stub_litellm_handler),
        retriever=FakeRetriever(doc.id),
        llm_streamer=FakeStreamer(),
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
    telegram_client: httpx.AsyncClient, telegram_env, outbound: OutboundRecorder
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
    assert outbound.calls == []


async def test_missing_signature_returns_401(
    telegram_client: httpx.AsyncClient, telegram_env, outbound: OutboundRecorder
) -> None:
    integration, _doc = telegram_env
    r = await telegram_client.post(
        f"/external/bots/telegram/{integration.webhook_id}", content=_update_body("x"),
    )
    assert r.status_code == 401
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
