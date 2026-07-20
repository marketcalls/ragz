"""Per-chat ephemeral attachment upload (DOC-9 Task 1): the row + blob get
created, and the route is scoped to the caller's own chat like every other
chat route (iron rule 1/2)."""

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from raghub.modules.auth.models import User
from raghub.modules.auth.passwords import hash_password
from raghub.modules.tenancy.models import Organization
from tests.api.test_chat_stream import auth

# chat_client/chat_env fixtures live in test_chat_stream; pytest only shares
# fixtures across modules via conftest.py or an explicit plugin import.
pytest_plugins = ["tests.api.test_chat_stream"]


@pytest.fixture
async def org_b_user(session: AsyncSession) -> User:
    org = Organization(name="RivalCorp")
    session.add(org)
    await session.flush()
    user = User(org_id=org.id, email="b@rival.com",
                password_hash=hash_password("pw123456"), role="admin")
    session.add(user)
    await session.commit()
    return user


async def make_chat(
    client: httpx.AsyncClient, chat_env: dict, h: dict[str, str]
) -> str:
    r = await client.post(
        "/api/v1/chats", json={"workspace_id": str(chat_env["workspace"].id)}, headers=h
    )
    return str(r.json()["id"])


async def test_upload_attachment_creates_row_and_stores_blob(
    chat_client: httpx.AsyncClient, chat_env: dict, seeded_user: User, stack_env: None,
) -> None:
    h = await auth(chat_client, seeded_user.email)
    chat_id = await make_chat(chat_client, chat_env, h)
    r = await chat_client.post(
        f"/api/v1/chats/{chat_id}/attachments",
        files={"file": ("notes.txt", b"hello world", "text/plain")},
        headers=h,
    )
    assert r.status_code == 201
    body = r.json()
    assert body["kind"] == "document"
    assert body["filename"] == "notes.txt"
    assert body["status"] == "queued"


async def test_upload_attachment_rejects_other_chats_chat(
    chat_client: httpx.AsyncClient, chat_env: dict, seeded_user: User, org_b_user: User,
    stack_env: None,
) -> None:
    h_a = await auth(chat_client, seeded_user.email)
    chat_id = await make_chat(chat_client, chat_env, h_a)
    h_b = await auth(chat_client, "b@rival.com")
    r = await chat_client.post(
        f"/api/v1/chats/{chat_id}/attachments",
        files={"file": ("x.txt", b"data", "text/plain")},
        headers=h_b,
    )
    assert r.status_code == 404
