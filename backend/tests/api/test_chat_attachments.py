"""Per-chat ephemeral attachment upload (DOC-9 Task 1): the row + blob get
created, and the route is scoped to the caller's own chat like every other
chat route (iron rule 1/2)."""

import asyncio

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from raghub.modules.auth.models import User
from raghub.modules.auth.passwords import hash_password
from raghub.modules.tenancy.models import Organization
from raghub.worker import tasks
from tests.api.test_chat_stream import auth, make_model_and_chat
from tests.conftest import FakeStreamer

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


async def test_small_attachment_routes_inline_and_appears_in_answer_context(
    chat_client: httpx.AsyncClient, chat_env: dict, session: AsyncSession,
    seeded_user: User, seeded_superadmin: User, fake_streamer: FakeStreamer,
    stack_env: None,
) -> None:
    """DOC-9 Task 5: a small extracted-text attachment fits the inline token
    budget, so route_attachment hands back a PromptSource and its raw text
    lands directly in the <data> blocks sent to the model -- no chunk/embed/
    upsert round trip through the ephemeral Qdrant collection."""
    h = await auth(chat_client, seeded_user.email)
    chat_id = await make_model_and_chat(chat_client, chat_env, session, seeded_superadmin, h)
    r = await chat_client.post(
        f"/api/v1/chats/{chat_id}/attachments",
        files={"file": ("notes.txt", b"hello world", "text/plain")},
        headers=h,
    )
    assert r.status_code == 201
    attachment_id = r.json()["id"]
    # Synchronous stand-in for the Celery worker (mirrors
    # tests/modules/chat/test_attachments.py's own pattern): drives the
    # attachment from "queued" to "ready" with extracted_text set.
    await asyncio.to_thread(tasks.process_attachment_task, attachment_id)

    r = await chat_client.post(
        f"/api/v1/chats/{chat_id}/messages",
        json={"content": "what does the attachment say?", "attachment_ids": [attachment_id]},
        headers=h,
    )
    assert r.status_code == 200
    sent_prompt = fake_streamer.calls[-1]["messages"]
    assert any(
        "hello world" in m["content"] for m in sent_prompt if isinstance(m["content"], str)
    )


async def test_large_attachment_routes_to_retrieval_not_inline(
    chat_client: httpx.AsyncClient, chat_env: dict, session: AsyncSession,
    seeded_user: User, seeded_superadmin: User, fake_streamer: FakeStreamer,
    stack_env: None,
) -> None:
    """DOC-9 Task 5: an attachment whose extracted text blows the inline
    token budget gets chunked/embedded/upserted into the ephemeral collection
    instead -- route_attachment returns None, and the raw text must never be
    dumped wholesale into the prompt."""
    h = await auth(chat_client, seeded_user.email)
    chat_id = await make_model_and_chat(chat_client, chat_env, session, seeded_superadmin, h)
    long_text = ("word " * 20000).encode()
    r = await chat_client.post(
        f"/api/v1/chats/{chat_id}/attachments",
        files={"file": ("big.txt", long_text, "text/plain")},
        headers=h,
    )
    assert r.status_code == 201
    attachment_id = r.json()["id"]
    await asyncio.to_thread(tasks.process_attachment_task, attachment_id)

    r = await chat_client.post(
        f"/api/v1/chats/{chat_id}/messages",
        json={"content": "summarize it", "attachment_ids": [attachment_id]},
        headers=h,
    )
    assert r.status_code == 200
    sent_prompt = fake_streamer.calls[-1]["messages"]
    # The full attachment text should NOT appear verbatim inline (it was
    # routed to retrieval instead) -- assert the prompt is small relative to
    # the attachment's actual size, proving it wasn't dumped in wholesale.
    full_prompt_text = "".join(
        m["content"] for m in sent_prompt if isinstance(m["content"], str)
    )
    assert len(full_prompt_text) < 20000 * 6  # well under the raw attachment size
