"""RBAC-08 adversarial proof: DELETE /workspaces/{id}/members/{user_id}
(Task 11) denies the removed user's access on EVERY entry point -- human JWT
(UI), external API key, and bot -- with no separate revocation step required.
Both `build_context_for_user` (human requests, `tenancy/context.py`) and
`build_verified_principal_context` (API-key/bot requests, RBAC-02) rebuild
the context from CURRENT `WorkspaceMember` rows on every single request, so
`service.remove_member` (Task 11) deleting the row is already the complete,
immediate revocation -- this suite proves it end to end, non-vacuously (every
BEFORE call must genuinely succeed before the AFTER call is asserted denied).

Mirrors `test_credential_revalidation.py`'s environment-building pattern
(one workspace + one indexed Document + FakeRetriever/FakeStreamer wiring so
a real chat answer is possible) and reuses `assign_contributor_role` (
`tests/conftest.py`) so the plain member's BEFORE requests legitimately hold
`documents.list`/`chat.generate` under the RBAC-04 deny-by-default floor."""

from uuid import uuid4

import httpx
import pytest
from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from ragz.api.app import create_app
from ragz.api.bots_relay import answer_for_integration
from ragz.core.config import Settings, get_settings
from ragz.core.db import build_session_factory
from ragz.core.errors import AuthenticationError
from ragz.modules.auth.api_keys_service import generate_api_key
from ragz.modules.auth.models import User
from ragz.modules.bots import service as bots_service
from ragz.modules.documents.models import Document
from ragz.modules.models.models import Model
from ragz.modules.tenancy.models import Workspace, WorkspaceMember
from tests.conftest import (
    FakeRetriever,
    FakeStreamer,
    _stub_litellm_handler,
    assign_contributor_role,
)


@pytest.fixture
async def revocation_env(session: AsyncSession, seeded_user: User) -> dict[str, object]:
    """One workspace, one plain role="user" member (given the migration-
    equivalent Contributor role so their BEFORE-removal requests genuinely
    succeed), plus a real indexed Document so FakeRetriever's chunks resolve
    and a chat answer can actually be produced."""
    ws = Workspace(org_id=seeded_user.org_id, name="RevocationWS")
    session.add(ws)
    await session.flush()
    model = Model(
        litellm_model_name=f"revoke-{ws.id}", display_name="Revoke", provider_kind="ollama"
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

    member = User(
        org_id=seeded_user.org_id, email="revocation-member@acme.com",
        password_hash=seeded_user.password_hash, role="user",
    )
    session.add(member)
    await session.flush()
    await assign_contributor_role(session, member)
    session.add(WorkspaceMember(workspace_id=ws.id, user_id=member.id))
    await session.commit()

    return {"workspace": ws, "document": doc, "member": member}


@pytest.fixture
async def revocation_app(
    engine: AsyncEngine, redis_client: object, revocation_env: dict[str, object]
):
    document = revocation_env["document"]
    assert isinstance(document, Document)
    app = create_app(
        session_factory=build_session_factory(engine),
        redis_client=redis_client,
        litellm_transport=httpx.MockTransport(_stub_litellm_handler),
        retriever=FakeRetriever(document.id),
        llm_streamer=FakeStreamer(),
    )
    app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None)
    return app


@pytest.fixture
async def revocation_client(revocation_app: object) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=revocation_app)  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
async def revocation_admin_headers(
    revocation_client: httpx.AsyncClient, seeded_user: User
) -> dict[str, str]:
    r = await revocation_client.post(
        "/api/v1/auth/login", json={"email": seeded_user.email, "password": "pw123456"}
    )
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture
async def revocation_member_headers(
    revocation_client: httpx.AsyncClient, revocation_env: dict[str, object]
) -> dict[str, str]:
    member = revocation_env["member"]
    assert isinstance(member, User)
    r = await revocation_client.post(
        "/api/v1/auth/login", json={"email": member.email, "password": "pw123456"}
    )
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


# ---------------------------------------------------------------------------
# 1. UI (human JWT): the removed member's next GET is denied.
# ---------------------------------------------------------------------------


async def test_removed_member_denied_on_next_ui_request(
    revocation_client: httpx.AsyncClient,
    revocation_admin_headers: dict[str, str],
    revocation_member_headers: dict[str, str],
    revocation_env: dict[str, object],
) -> None:
    ws = revocation_env["workspace"]
    member = revocation_env["member"]
    assert isinstance(ws, Workspace)
    assert isinstance(member, User)

    before = await revocation_client.get(
        f"/api/v1/workspaces/{ws.id}/documents", headers=revocation_member_headers
    )
    assert before.status_code == 200, before.text

    removed = await revocation_client.delete(
        f"/api/v1/workspaces/{ws.id}/members/{member.id}", headers=revocation_admin_headers
    )
    assert removed.status_code == 204, removed.text

    after = await revocation_client.get(
        f"/api/v1/workspaces/{ws.id}/documents", headers=revocation_member_headers
    )
    assert after.status_code in (403, 404), after.text


# ---------------------------------------------------------------------------
# 2. External API key: the removed member's already-issued key is denied on
#    the very next call -- no separate key-revocation step.
# ---------------------------------------------------------------------------


async def test_removed_member_denies_their_api_key_immediately(
    session: AsyncSession,
    revocation_client: httpx.AsyncClient,
    revocation_admin_headers: dict[str, str],
    revocation_env: dict[str, object],
    seeded_user: User,
) -> None:
    ws = revocation_env["workspace"]
    member = revocation_env["member"]
    assert isinstance(ws, Workspace)
    assert isinstance(member, User)
    settings = Settings(_env_file=None)
    _, raw_key = await generate_api_key(
        session, settings, actor_id=seeded_user.id, name="member-key",
        user_id=member.id, workspace_id=ws.id, expires_at=None,
    )

    before = await revocation_client.post(
        "/external/v1/chat", json={"question": "hi"},
        headers={"Authorization": f"Bearer {raw_key}"},
    )
    assert before.status_code == 200, before.text
    assert before.json()["answer"]

    removed = await revocation_client.delete(
        f"/api/v1/workspaces/{ws.id}/members/{member.id}", headers=revocation_admin_headers
    )
    assert removed.status_code == 204, removed.text

    after = await revocation_client.post(
        "/external/v1/chat", json={"question": "hi again"},
        headers={"Authorization": f"Bearer {raw_key}"},
    )
    assert after.status_code == 401, after.text


# ---------------------------------------------------------------------------
# 3. Bot: `answer_for_integration` raises `AuthenticationError` for the
#    removed member's integration on the very next inbound message.
#    `build_verified_principal_context` (which raises) runs BEFORE
#    `answer_for_integration` ever touches `request` -- so a manually built
#    `Request` bound to the real, fully-wired app (never dereferenced further
#    than that on this path) stands in for the framework-injected one.
# ---------------------------------------------------------------------------


async def test_removed_member_denies_their_bot_immediately(
    session: AsyncSession,
    revocation_admin_headers: dict[str, str],
    revocation_client: httpx.AsyncClient,
    revocation_app: object,
    revocation_env: dict[str, object],
    seeded_user: User,
) -> None:
    ws = revocation_env["workspace"]
    member = revocation_env["member"]
    assert isinstance(ws, Workspace)
    assert isinstance(member, User)
    settings = Settings(_env_file=None)
    integration = await bots_service.create_integration(
        session, settings, actor_id=seeded_user.id, platform="telegram", name="revoke-bot",
        workspace_id=ws.id, user_id=member.id, token="tok",  # noqa: S106
        signing_secret="sig",  # noqa: S106
    )
    fake_request = Request(scope={"type": "http", "app": revocation_app})

    before_answer = await answer_for_integration(
        fake_request, session, get_settings(), integration,
        external_chat_id="chat-1", text="hi",
    )
    assert before_answer

    removed = await revocation_client.delete(
        f"/api/v1/workspaces/{ws.id}/members/{member.id}", headers=revocation_admin_headers
    )
    assert removed.status_code == 204, removed.text

    with pytest.raises(AuthenticationError):
        await answer_for_integration(
            fake_request, session, get_settings(), integration,
            external_chat_id="chat-1", text="hi again",
        )
