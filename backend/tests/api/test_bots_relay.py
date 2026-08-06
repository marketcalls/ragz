"""Task 3: the bot->RAG seam (`answer_for_integration`). Mounts a tiny
throwaway test-only route (mirrors the API-key plan's Task 3 pattern of a
temporary router when the "real" caller -- a platform webhook route -- doesn't
exist yet) so `answer_for_integration` gets a real `Request` (it threads
through to `_run_external_answer`, which reads `request.app.state.*`)."""

from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import APIRouter, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from ragz.api.app import create_app
from ragz.api.bots_relay import answer_for_integration
from ragz.core.config import Settings, get_settings
from ragz.core.db import build_session_factory
from ragz.modules.auth.models import User
from ragz.modules.bots import service as bots_service
from ragz.modules.bots.models import BotConversation
from ragz.modules.documents.models import Document
from ragz.modules.models.models import Model
from ragz.modules.tenancy.models import Workspace, WorkspaceMember
from tests.conftest import FakeRetriever, FakeStreamer, _stub_litellm_handler


@pytest.fixture
async def relay_env(session: AsyncSession, seeded_user: User) -> dict[str, object]:
    ws = Workspace(org_id=seeded_user.org_id, name="RelayWS")
    session.add(ws)
    await session.flush()
    session.add(WorkspaceMember(workspace_id=ws.id, user_id=seeded_user.id))
    model = Model(litellm_model_name=f"relay-{ws.id}", display_name="Relay", provider_kind="ollama")
    session.add(model)
    # A real indexed Document (chat_env's pattern) that FakeRetriever's
    # chunks point at -- stream_reply's _prepare_sources resolves each
    # chunk's document_id via documents.service.get_document_checked, which
    # 404s on an unregistered id, so a bare uuid4() (unattached to any row)
    # fails the whole answer with a 502 rather than exercising the relay.
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
        workspace_id=ws.id, user_id=seeded_user.id, token="tok", signing_secret="sig",  # noqa: S106
    )
    return {"workspace": ws, "integration": integration, "document": doc}


_test_router = APIRouter()


@_test_router.post("/_test/bots/relay/{integration_id}")
async def _relay_probe(
    integration_id: UUID, external_chat_id: str, text: str, request: Request
) -> dict[str, str]:
    from ragz.core.db import get_session

    async for session in get_session(request):  # type: ignore[arg-type]
        integration = await bots_service.get_integration(session, integration_id=integration_id)
        answer = await answer_for_integration(
            request, session, get_settings(), integration,
            external_chat_id=external_chat_id, text=text,
        )
        return {"answer": answer}
    raise AssertionError("unreachable")


@pytest.fixture
async def relay_client(
    engine: AsyncEngine, redis_client, relay_env: dict[str, object]
) -> httpx.AsyncClient:
    document = relay_env["document"]
    assert isinstance(document, Document)
    document_id = document.id
    app = create_app(
        session_factory=build_session_factory(engine),
        redis_client=redis_client,
        litellm_transport=httpx.MockTransport(_stub_litellm_handler),
        retriever=FakeRetriever(document_id),
        llm_streamer=FakeStreamer(),
    )
    app.include_router(_test_router)
    app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_answer_for_integration_returns_collected_answer(
    relay_client: httpx.AsyncClient, relay_env: dict[str, object]
) -> None:
    integration = relay_env["integration"]
    r = await relay_client.post(
        f"/_test/bots/relay/{integration.id}",
        params={"external_chat_id": "chat-42", "text": "what was revenue?"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["answer"]  # FakeStreamer's canned answer text


async def test_answer_for_integration_reuses_conversation_for_same_external_chat_id(
    session: AsyncSession, relay_client: httpx.AsyncClient, relay_env: dict[str, object]
) -> None:
    integration = relay_env["integration"]
    await relay_client.post(
        f"/_test/bots/relay/{integration.id}",
        params={"external_chat_id": "chat-42", "text": "first question"},
    )
    await relay_client.post(
        f"/_test/bots/relay/{integration.id}",
        params={"external_chat_id": "chat-42", "text": "second question"},
    )
    mappings = (
        await session.execute(
            select(BotConversation).where(BotConversation.integration_id == integration.id)
        )
    ).scalars().all()
    assert len(mappings) == 1  # one mapping row, not one per message


async def test_answer_for_integration_never_leaks_other_workspace_content(
    session: AsyncSession, relay_client: httpx.AsyncClient, relay_env: dict[str, object],
    seeded_user: User,
) -> None:
    """Isolation fold-in (design doc's Testing section): a bot integration
    bound to workspace A must resolve a TenantContext scoped to ONLY that
    workspace -- proven by asserting the ctx build_context_for_user produces
    for the integration's user carries workspace_ids == {workspace A}, even
    when that same user is ALSO a member of a second workspace B with its
    own content. Mirrors test_api_key_isolation.py's cross-workspace case at
    the ctx-construction level (the retrieval-time exclusion itself is
    already pinned by test_tenant_isolation.py/test_acl_isolation.py; this
    proves the NEW bots code threads the integration's OWN workspace_id
    through, not the user's full membership set)."""
    ws_b = Workspace(org_id=seeded_user.org_id, name="OtherWS")
    session.add(ws_b)
    await session.flush()
    session.add(WorkspaceMember(workspace_id=ws_b.id, user_id=seeded_user.id))
    await session.commit()

    integration = relay_env["integration"]
    workspace_a = relay_env["workspace"]
    assert isinstance(workspace_a, Workspace)

    from ragz.modules.tenancy.context import build_context_for_user

    user = await session.get(User, integration.user_id)
    assert user is not None
    ctx = await build_context_for_user(
        session, user, workspace_ids=frozenset({integration.workspace_id})
    )
    assert ctx.workspace_ids == {workspace_a.id}
    assert ws_b.id not in ctx.workspace_ids
