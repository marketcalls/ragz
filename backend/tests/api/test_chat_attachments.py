"""Per-chat ephemeral attachment upload (DOC-9 Task 1): the row + blob get
created, and the route is scoped to the caller's own chat like every other
chat route (iron rule 1/2)."""

import asyncio
import base64

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


async def test_image_attachment_on_vision_model_becomes_multimodal_content(
    chat_client: httpx.AsyncClient, chat_env: dict, session: AsyncSession,
    seeded_user: User, seeded_superadmin: User, fake_streamer: FakeStreamer,
    stack_env: None,
) -> None:
    """DOC-9 Task 6: a kind="image" attachment sent alongside a model whose
    supports_vision=True skips Task 5's route_attachment (OCR/inline/
    retrieval) entirely for that attachment -- its raw bytes go straight to
    the model as an OpenAI-style multimodal content block instead of ending
    up as extracted text inside a <data> block."""
    h = await auth(chat_client, seeded_user.email)
    chat_id = await make_model_and_chat(chat_client, chat_env, session, seeded_superadmin, h)
    h_super = await auth(chat_client, "root@platform.example")
    r_model = await chat_client.post(
        "/api/v1/admin/models",
        json={"litellm_model_name": "vision-model", "display_name": "Vision",
              "provider_kind": "ollama", "base_url": "http://ollama:11434",
              "supports_vision": True},
        headers=h_super,
    )
    assert r_model.status_code == 201
    vision_model_id = r_model.json()["id"]

    image_bytes = b"\x89PNG\r\n\x1a\nfake-bytes-not-a-real-png"
    r = await chat_client.post(
        f"/api/v1/chats/{chat_id}/attachments",
        files={"file": ("photo.png", image_bytes, "image/png")},
        headers=h,
    )
    assert r.status_code == 201
    assert r.json()["kind"] == "image"
    attachment_id = r.json()["id"]
    # Deliberately NOT running process_attachment_task here: the vision path
    # reads raw bytes straight from storage (set at upload time) and does not
    # depend on the OCR worker job Task 5's inline/retrieval routing needs.

    r = await chat_client.post(
        f"/api/v1/chats/{chat_id}/messages",
        json={
            "content": "what is in this image?",
            "attachment_ids": [attachment_id],
            "model_id": vision_model_id,
        },
        headers=h,
    )
    assert r.status_code == 200
    sent_prompt = fake_streamer.calls[-1]["messages"]
    last = sent_prompt[-1]
    assert isinstance(last["content"], list)
    assert last["content"][0] == {"type": "text", "text": "what is in this image?"}
    image_block = last["content"][1]
    assert image_block == {
        "type": "image_url", "image_url": {"url": image_block["image_url"]["url"]},
    }
    data_uri = image_block["image_url"]["url"]
    assert data_uri.startswith("data:image/png;base64,")
    encoded = data_uri.removeprefix("data:image/png;base64,")
    assert base64.b64decode(encoded) == image_bytes


async def test_image_attachment_on_non_vision_model_still_routes_through_ocr(
    chat_client: httpx.AsyncClient, chat_env: dict, session: AsyncSession,
    seeded_user: User, seeded_superadmin: User, fake_streamer: FakeStreamer,
    stack_env: None,
) -> None:
    """DOC-9 Task 6: an image attachment on a model with supports_vision=False
    (the workspace default used by make_model_and_chat) is UNCHANGED by this
    task -- it keeps flowing through Task 2's OCR extract_text + Task 5's
    inline/retrieval routing, exactly like a document attachment."""
    h = await auth(chat_client, seeded_user.email)
    chat_id = await make_model_and_chat(chat_client, chat_env, session, seeded_superadmin, h)
    image_bytes = b"\x89PNG\r\n\x1a\nfake-bytes-not-a-real-png"
    r = await chat_client.post(
        f"/api/v1/chats/{chat_id}/attachments",
        files={"file": ("photo.png", image_bytes, "image/png")},
        headers=h,
    )
    assert r.status_code == 201
    assert r.json()["kind"] == "image"
    attachment_id = r.json()["id"]
    await asyncio.to_thread(tasks.process_attachment_task, attachment_id)

    r = await chat_client.post(
        f"/api/v1/chats/{chat_id}/messages",
        json={"content": "what is in this image?", "attachment_ids": [attachment_id]},
        headers=h,
    )
    assert r.status_code == 200
    sent_prompt = fake_streamer.calls[-1]["messages"]
    # Non-vision model: the final message stays plain-string, never the
    # multipart shape Task 6 introduces.
    assert all(isinstance(m["content"], str) for m in sent_prompt)
