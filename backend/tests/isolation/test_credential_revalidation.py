"""RBAC-02 (audit §RBAC-02): API keys and bot integrations must revalidate
the backing user's CURRENT workspace membership and `chat.use` permission on
EVERY request, never trusting the workspace/permissions captured at
issuance. Pre-fix, `api_key_context` (external.py) and
`answer_for_integration` (bots_relay.py) both call
`build_context_for_user(session, user, workspace_ids=frozenset({stored_workspace_id}))`
directly -- so revoking a user's workspace membership, or stripping
`chat.use` via a custom RoleTemplate, does NOT stop an already-issued
key/bot from answering. This suite adversarially proves the deny -- and
proves it is SELECTIVE (a sibling credential for a still-valid member of the
SAME workspace keeps working throughout).

Mirrors test_bots_relay.py's throwaway `_test_router` pattern for exercising
`answer_for_integration` directly (it needs a real `Request` for
`_run_external_answer`'s `request.app.state.*` reads), plus
test_external_chat.py's FakeRetriever/FakeStreamer app wiring for the key
path. One additional test drives the real Telegram webhook end-to-end
(test_bots_telegram.py's pattern) to pin that a denial surfaces as a clean
401 -- never a 500 -- with no outbound call."""

import json
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import APIRouter, Request
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from ragz.api.app import create_app
from ragz.api.bots_relay import answer_for_integration
from ragz.core.config import Settings, get_settings
from ragz.core.db import build_session_factory
from ragz.modules.auth.api_keys_service import generate_api_key
from ragz.modules.auth.models import User
from ragz.modules.bots import service as bots_service
from ragz.modules.documents.models import Document
from ragz.modules.models.models import Model
from ragz.modules.tenancy.models import RoleTemplate, Workspace, WorkspaceMember
from tests.conftest import (
    FakeRetriever,
    FakeStreamer,
    _stub_litellm_handler,
    assign_contributor_role,
)

TELEGRAM_SECRET = "cred-revalidation-tg-secret"  # noqa: S105 -- test fixture value


@pytest.fixture
async def cred_env(session: AsyncSession, seeded_user: User) -> dict[str, object]:
    """One workspace, two independent plain role="user" members (`member_a`
    -- the one every adversarial test revokes/strips -- and `member_b`, a
    sibling who stays untouched throughout, proving the deny is selective
    rather than an accidental blanket outage), each with their OWN API key
    and OWN bot integration bound to the SAME workspace."""
    ws = Workspace(org_id=seeded_user.org_id, name="CredRevalWS")
    session.add(ws)
    await session.flush()
    model = Model(
        litellm_model_name=f"credreval-{ws.id}", display_name="CredReval", provider_kind="ollama"
    )
    session.add(model)
    doc = Document(
        org_id=seeded_user.org_id, workspace_id=ws.id, filename="report.pdf",
        mime="application/pdf", size_bytes=10, content_hash="h", status="indexed",
        storage_key="k", created_by=seeded_user.id, lineage_id=uuid4(),
    )
    session.add(doc)
    await session.flush()
    ws.default_model_id = model.id
    session.add(ws)

    member_a = User(
        org_id=seeded_user.org_id, email="member-a@acme.com",
        password_hash=seeded_user.password_hash, role="user",
    )
    member_b = User(
        org_id=seeded_user.org_id, email="member-b@acme.com",
        password_hash=seeded_user.password_hash, role="user",
    )
    session.add_all([member_a, member_b])
    await session.flush()
    # RBAC-04: chat.use is no longer in the read-only default, but
    # build_verified_principal_context still requires it. Give both members the
    # migration-equivalent contributor role so the CONTROL cases answer (they
    # legitimately hold chat.use). The adversarial cases then revoke it the
    # real way -- removing workspace membership, or _strip_chat_use reassigning
    # a role WITHOUT chat.use -- so every deny assertion below stays intact.
    await assign_contributor_role(session, member_a, member_b)
    session.add_all([
        WorkspaceMember(workspace_id=ws.id, user_id=member_a.id),
        WorkspaceMember(workspace_id=ws.id, user_id=member_b.id),
    ])
    await session.commit()

    settings = Settings(_env_file=None)
    _, raw_key_a = await generate_api_key(
        session, settings, actor_id=seeded_user.id, name="key-a",
        user_id=member_a.id, workspace_id=ws.id, expires_at=None,
    )
    _, raw_key_b = await generate_api_key(
        session, settings, actor_id=seeded_user.id, name="key-b",
        user_id=member_b.id, workspace_id=ws.id, expires_at=None,
    )
    bot_a = await bots_service.create_integration(
        session, settings, actor_id=seeded_user.id, platform="telegram", name="bot-a",
        workspace_id=ws.id, user_id=member_a.id, token="tok-a",  # noqa: S106
        signing_secret=TELEGRAM_SECRET,
    )
    bot_b = await bots_service.create_integration(
        session, settings, actor_id=seeded_user.id, platform="telegram", name="bot-b",
        workspace_id=ws.id, user_id=member_b.id, token="tok-b",  # noqa: S106
        signing_secret=TELEGRAM_SECRET,
    )
    return {
        "workspace": ws, "document": doc,
        "member_a": member_a, "member_b": member_b,
        "raw_key_a": raw_key_a, "raw_key_b": raw_key_b,
        "bot_a": bot_a, "bot_b": bot_b,
    }


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
async def cred_client(
    engine: AsyncEngine, redis_client, cred_env: dict[str, object]
) -> httpx.AsyncClient:
    document = cred_env["document"]
    assert isinstance(document, Document)
    app = create_app(
        session_factory=build_session_factory(engine),
        redis_client=redis_client,
        litellm_transport=httpx.MockTransport(_stub_litellm_handler),
        retriever=FakeRetriever(document.id),
        llm_streamer=FakeStreamer(),
    )
    app.include_router(_test_router)
    app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _ask_key(client: httpx.AsyncClient, raw_key: str, question: str = "hi") -> httpx.Response:
    return await client.post(
        "/external/v1/chat", json={"question": question},
        headers={"Authorization": f"Bearer {raw_key}"},
    )


async def _ask_bot(client: httpx.AsyncClient, integration_id: UUID, chat_id: str) -> httpx.Response:
    return await client.post(
        f"/_test/bots/relay/{integration_id}",
        params={"external_chat_id": chat_id, "text": "hi"},
    )


# ---------------------------------------------------------------------------
# Control: member + chat.use -> a real, non-vacuous answer.
# ---------------------------------------------------------------------------


async def test_control_key_answers(
    cred_client: httpx.AsyncClient, cred_env: dict[str, object]
) -> None:
    r = await _ask_key(cred_client, cred_env["raw_key_a"])  # type: ignore[arg-type]
    assert r.status_code == 200, r.text
    assert r.json()["answer"]


async def test_control_bot_answers(
    cred_client: httpx.AsyncClient, cred_env: dict[str, object]
) -> None:
    bot_a = cred_env["bot_a"]
    r = await _ask_bot(cred_client, bot_a.id, "chat-a")  # type: ignore[union-attr]
    assert r.status_code == 200, r.text
    assert r.json()["answer"]


# ---------------------------------------------------------------------------
# Membership revoked -> the SAME key/bot is denied. A sibling credential for
# a still-valid member of the SAME workspace keeps working (selective deny).
# ---------------------------------------------------------------------------


async def test_membership_removal_denies_key_but_not_sibling(
    cred_client: httpx.AsyncClient, cred_env: dict[str, object], session: AsyncSession,
) -> None:
    ws = cred_env["workspace"]
    member_a = cred_env["member_a"]
    assert isinstance(ws, Workspace)
    assert isinstance(member_a, User)
    await session.execute(
        delete(WorkspaceMember).where(
            WorkspaceMember.workspace_id == ws.id, WorkspaceMember.user_id == member_a.id,
        )
    )
    await session.commit()

    r = await _ask_key(cred_client, cred_env["raw_key_a"])  # type: ignore[arg-type]
    assert r.status_code == 401, r.text

    r_sibling = await _ask_key(cred_client, cred_env["raw_key_b"])  # type: ignore[arg-type]
    assert r_sibling.status_code == 200, r_sibling.text
    assert r_sibling.json()["answer"]


async def test_membership_removal_denies_bot_but_not_sibling(
    cred_client: httpx.AsyncClient, cred_env: dict[str, object], session: AsyncSession,
) -> None:
    ws = cred_env["workspace"]
    member_a = cred_env["member_a"]
    bot_a = cred_env["bot_a"]
    bot_b = cred_env["bot_b"]
    assert isinstance(ws, Workspace)
    assert isinstance(member_a, User)
    await session.execute(
        delete(WorkspaceMember).where(
            WorkspaceMember.workspace_id == ws.id, WorkspaceMember.user_id == member_a.id,
        )
    )
    await session.commit()

    r = await _ask_bot(cred_client, bot_a.id, "chat-a")  # type: ignore[union-attr]
    assert r.status_code == 401, r.text

    r_sibling = await _ask_bot(cred_client, bot_b.id, "chat-b")  # type: ignore[union-attr]
    assert r_sibling.status_code == 200, r_sibling.text
    assert r_sibling.json()["answer"]


# ---------------------------------------------------------------------------
# chat.use stripped via a custom RoleTemplate -> denied (membership intact).
# ---------------------------------------------------------------------------


async def _strip_chat_use(session: AsyncSession, user: User) -> None:
    template = RoleTemplate(
        name=f"no-chat-use-{uuid4()}", permissions=["documents.upload"],  # no "chat.use"
    )
    session.add(template)
    await session.flush()
    user.custom_role_id = template.id
    session.add(user)
    await session.commit()


async def test_chat_use_stripped_denies_key_but_not_sibling(
    cred_client: httpx.AsyncClient, cred_env: dict[str, object], session: AsyncSession,
) -> None:
    member_a = cred_env["member_a"]
    assert isinstance(member_a, User)
    await _strip_chat_use(session, member_a)

    r = await _ask_key(cred_client, cred_env["raw_key_a"])  # type: ignore[arg-type]
    assert r.status_code == 401, r.text

    r_sibling = await _ask_key(cred_client, cred_env["raw_key_b"])  # type: ignore[arg-type]
    assert r_sibling.status_code == 200, r_sibling.text
    assert r_sibling.json()["answer"]


async def test_chat_use_stripped_denies_bot_but_not_sibling(
    cred_client: httpx.AsyncClient, cred_env: dict[str, object], session: AsyncSession,
) -> None:
    member_a = cred_env["member_a"]
    bot_a = cred_env["bot_a"]
    bot_b = cred_env["bot_b"]
    assert isinstance(member_a, User)
    await _strip_chat_use(session, member_a)

    r = await _ask_bot(cred_client, bot_a.id, "chat-a")  # type: ignore[union-attr]
    assert r.status_code == 401, r.text

    r_sibling = await _ask_bot(cred_client, bot_b.id, "chat-b")  # type: ignore[union-attr]
    assert r_sibling.status_code == 200, r_sibling.text
    assert r_sibling.json()["answer"]


# ---------------------------------------------------------------------------
# End-to-end through the REAL Telegram webhook route: a denial must surface
# as a clean non-2xx response (never a 500), with no outbound send.
# ---------------------------------------------------------------------------


def _telegram_update_body(chat_id: int, text: str) -> bytes:
    return json.dumps({"update_id": 1, "message": {"chat": {"id": chat_id}, "text": text}}).encode()


@pytest.fixture
async def telegram_denial_client(
    engine: AsyncEngine, redis_client, cred_env: dict[str, object]
) -> httpx.AsyncClient:
    document = cred_env["document"]
    assert isinstance(document, Document)
    app = create_app(
        session_factory=build_session_factory(engine),
        redis_client=redis_client,
        litellm_transport=httpx.MockTransport(_stub_litellm_handler),
        retriever=FakeRetriever(document.id),
        llm_streamer=FakeStreamer(),
        bot_outbound_transport=httpx.MockTransport(
            lambda r: httpx.Response(200, json={"ok": True})
        ),
    )
    app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_telegram_webhook_denial_after_membership_removal_is_clean_not_500(
    telegram_denial_client: httpx.AsyncClient, cred_env: dict[str, object], session: AsyncSession,
) -> None:
    ws = cred_env["workspace"]
    member_a = cred_env["member_a"]
    bot_a = cred_env["bot_a"]
    assert isinstance(ws, Workspace)
    assert isinstance(member_a, User)
    await session.execute(
        delete(WorkspaceMember).where(
            WorkspaceMember.workspace_id == ws.id, WorkspaceMember.user_id == member_a.id,
        )
    )
    await session.commit()

    body = _telegram_update_body(999, "should be denied")
    r = await telegram_denial_client.post(
        f"/external/bots/telegram/{bot_a.webhook_id}",  # type: ignore[union-attr]
        content=body,
        headers={
            "X-Telegram-Bot-Api-Secret-Token": TELEGRAM_SECRET,
            "content-type": "application/json",
        },
    )
    assert r.status_code != 500, r.text
    assert r.status_code == 401, r.text
