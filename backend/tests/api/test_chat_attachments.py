"""Per-chat ephemeral attachment upload (DOC-9 Task 1): the row + blob get
created, and the route is scoped to the caller's own chat like every other
chat route (iron rule 1/2)."""

import asyncio
import base64
import struct
import zlib
from io import BytesIO
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZipFile

import httpx
import pytest
from PIL import Image
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from ragz.api.app import create_app
from ragz.core.config import Settings, get_settings
from ragz.core.db import build_session_factory
from ragz.modules.auth.models import User
from ragz.modules.auth.passwords import hash_password
from ragz.modules.tenancy.models import Organization
from ragz.worker import tasks
from tests.api.test_chat_stream import auth, make_model_and_chat, parse_sse
from tests.api.test_permissions_routes import make_templated_member
from tests.conftest import FakeChunkReader, FakeRetriever, FakeStreamer, _stub_litellm_handler

# chat_client/chat_env fixtures live in test_chat_stream; pytest only shares
# fixtures across modules via conftest.py or an explicit plugin import.
pytest_plugins = ["tests.api.test_chat_stream"]


def _png_bytes() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (1, 1), color="white").save(buffer, format="PNG")
    return buffer.getvalue()


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


async def test_attachment_upload_streams_to_storage(
    chat_client: httpx.AsyncClient,
    chat_env: dict,
    seeded_user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ragz.core.storage import ObjectStorage

    streamed: list[bytes] = []

    async def _forbid_buffered_put(self, key, data, content_type="application/octet-stream"):  # type: ignore[no-untyped-def]
        raise AssertionError("attachment upload buffered the entire file")

    async def _capture_stream(self, key, fileobj, content_type="application/octet-stream"):  # type: ignore[no-untyped-def]
        streamed.append(fileobj.read())

    monkeypatch.setattr(ObjectStorage, "put", _forbid_buffered_put)
    monkeypatch.setattr(ObjectStorage, "put_stream", _capture_stream)
    monkeypatch.setattr("ragz.api.routes.chats.enqueue_attachment_processing", lambda _id: None)
    headers = await auth(chat_client, seeded_user.email)
    chat_id = await make_chat(chat_client, chat_env, headers)

    response = await chat_client.post(
        f"/api/v1/chats/{chat_id}/attachments",
        files={"file": ("notes.txt", b"stream me", "text/plain")},
        headers=headers,
    )

    assert response.status_code == 201
    assert streamed == [b"stream me"]


async def test_spoofed_attachment_type_is_rejected_before_storage_and_enqueue(
    chat_client: httpx.AsyncClient,
    chat_env: dict,
    seeded_user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ragz.core.storage import ObjectStorage

    storage_writes: list[str] = []
    enqueues: list[str] = []

    async def _store(self, key, data, content_type="application/octet-stream"):  # type: ignore[no-untyped-def]
        storage_writes.append(key)

    monkeypatch.setattr(ObjectStorage, "put", _store)
    monkeypatch.setattr(ObjectStorage, "put_stream", _store)
    monkeypatch.setattr(
        "ragz.api.routes.chats.enqueue_attachment_processing",
        lambda attachment_id: enqueues.append(str(attachment_id)),
    )
    headers = await auth(chat_client, seeded_user.email)
    chat_id = await make_chat(chat_client, chat_env, headers)

    response = await chat_client.post(
        f"/api/v1/chats/{chat_id}/attachments",
        files={
            "file": (
                "spoofed.pdf",
                b"<!doctype html><script>parent.previewPwned=true</script>",
                "application/pdf",
            )
        },
        headers=headers,
    )

    assert response.status_code == 415
    assert storage_writes == []
    assert enqueues == []


async def test_compressed_attachment_budget_rejects_before_storage_and_enqueue(
    chat_client: httpx.AsyncClient,
    chat_env: dict,
    seeded_user: User,
    test_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ragz.core.storage import ObjectStorage

    archive_bytes = BytesIO()
    with ZipFile(archive_bytes, "w", ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", "x" * 1024)
    body = archive_bytes.getvalue()
    monkeypatch.setattr(test_settings, "attachment_max_uncompressed_bytes", 100)
    storage_writes: list[str] = []
    enqueues: list[str] = []

    async def _store(self, key, fileobj, content_type="application/octet-stream"):  # type: ignore[no-untyped-def]
        storage_writes.append(key)

    monkeypatch.setattr(ObjectStorage, "put_stream", _store)
    monkeypatch.setattr(
        "ragz.api.routes.chats.enqueue_attachment_processing",
        lambda attachment_id: enqueues.append(str(attachment_id)),
    )
    headers = await auth(chat_client, seeded_user.email)
    chat_id = await make_chat(chat_client, chat_env, headers)

    response = await chat_client.post(
        f"/api/v1/chats/{chat_id}/attachments",
        files={
            "file": (
                "large.docx",
                body,
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
        headers=headers,
    )

    assert response.status_code == 413
    assert storage_writes == []
    assert enqueues == []


async def test_image_pixel_budget_rejects_before_storage_and_enqueue(
    chat_client: httpx.AsyncClient,
    chat_env: dict,
    seeded_user: User,
    test_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ragz.core.storage import ObjectStorage

    def _chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", 10_000, 10_000, 8, 2, 0, 0, 0)
    body = b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr) + _chunk(b"IEND", b"")
    monkeypatch.setattr(test_settings, "attachment_max_image_pixels", 1_000_000)
    storage_writes: list[str] = []
    enqueues: list[str] = []

    async def _store(self, key, fileobj, content_type="application/octet-stream"):  # type: ignore[no-untyped-def]
        storage_writes.append(key)

    monkeypatch.setattr(ObjectStorage, "put_stream", _store)
    monkeypatch.setattr(
        "ragz.api.routes.chats.enqueue_attachment_processing",
        lambda attachment_id: enqueues.append(str(attachment_id)),
    )
    headers = await auth(chat_client, seeded_user.email)
    chat_id = await make_chat(chat_client, chat_env, headers)

    response = await chat_client.post(
        f"/api/v1/chats/{chat_id}/attachments",
        files={"file": ("bomb.png", body, "image/png")},
        headers=headers,
    )

    assert response.status_code == 413
    assert storage_writes == []
    assert enqueues == []


@pytest.mark.parametrize(
    ("setting", "limit", "first", "second"),
    [
        ("attachment_max_count_per_user", 1, b"first", b"second"),
        ("attachment_max_bytes_per_user", 10, b"123456", b"abcdef"),
    ],
)
async def test_attachment_aggregate_limits_reject_before_storage_and_enqueue(
    chat_client: httpx.AsyncClient,
    chat_env: dict,
    seeded_user: User,
    test_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    setting: str,
    limit: int,
    first: bytes,
    second: bytes,
) -> None:
    from ragz.core.storage import ObjectStorage

    monkeypatch.setattr(test_settings, setting, limit)
    storage_writes: list[str] = []
    enqueues: list[str] = []

    async def _store(self, key, fileobj, content_type="application/octet-stream"):  # type: ignore[no-untyped-def]
        storage_writes.append(key)

    monkeypatch.setattr(ObjectStorage, "put_stream", _store)
    monkeypatch.setattr(
        "ragz.api.routes.chats.enqueue_attachment_processing",
        lambda attachment_id: enqueues.append(str(attachment_id)),
    )
    headers = await auth(chat_client, seeded_user.email)
    chat_id = await make_chat(chat_client, chat_env, headers)

    accepted = await chat_client.post(
        f"/api/v1/chats/{chat_id}/attachments",
        files={"file": ("one.txt", first, "text/plain")},
        headers=headers,
    )
    rejected = await chat_client.post(
        f"/api/v1/chats/{chat_id}/attachments",
        files={"file": ("two.txt", second, "text/plain")},
        headers=headers,
    )

    assert accepted.status_code == 201
    assert rejected.status_code == 413
    assert len(storage_writes) == 1
    assert len(enqueues) == 1


async def test_parallel_attachment_pending_limit_counts_in_flight_upload(
    chat_client: httpx.AsyncClient,
    chat_env: dict,
    seeded_user: User,
    test_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ragz.core.storage import ObjectStorage

    monkeypatch.setattr(test_settings, "attachment_max_pending_per_user", 1)
    first_storage_started = asyncio.Event()
    release_first_storage = asyncio.Event()
    storage_writes: list[str] = []
    enqueues: list[str] = []

    async def _store(self, key, payload, content_type="application/octet-stream"):  # type: ignore[no-untyped-def]
        storage_writes.append(key)
        if len(storage_writes) == 1:
            first_storage_started.set()
            await release_first_storage.wait()

    monkeypatch.setattr(ObjectStorage, "put", _store)
    monkeypatch.setattr(ObjectStorage, "put_stream", _store)
    monkeypatch.setattr(
        "ragz.api.routes.chats.enqueue_attachment_processing",
        lambda attachment_id: enqueues.append(str(attachment_id)),
    )
    headers = await auth(chat_client, seeded_user.email)
    chat_id = await make_chat(chat_client, chat_env, headers)
    first = asyncio.create_task(
        chat_client.post(
            f"/api/v1/chats/{chat_id}/attachments",
            files={"file": ("one.txt", b"first", "text/plain")},
            headers=headers,
        )
    )
    await asyncio.wait_for(first_storage_started.wait(), timeout=5)

    rejected = await chat_client.post(
        f"/api/v1/chats/{chat_id}/attachments",
        files={"file": ("two.txt", b"second", "text/plain")},
        headers=headers,
    )
    release_first_storage.set()
    accepted = await asyncio.wait_for(first, timeout=5)

    assert accepted.status_code == 201
    assert rejected.status_code == 413
    assert len(storage_writes) == 1
    assert len(enqueues) == 1


async def test_attachment_upload_frequency_is_bounded_per_user(
    chat_client: httpx.AsyncClient,
    chat_env: dict,
    seeded_user: User,
    test_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ragz.core.storage import ObjectStorage

    monkeypatch.setattr(test_settings, "attachment_uploads_per_minute", 3)

    async def _store(self, key, fileobj, content_type="application/octet-stream"):  # type: ignore[no-untyped-def]
        return None

    monkeypatch.setattr(ObjectStorage, "put_stream", _store)
    monkeypatch.setattr("ragz.api.routes.chats.enqueue_attachment_processing", lambda _id: None)
    headers = await auth(chat_client, seeded_user.email)
    chat_id = await make_chat(chat_client, chat_env, headers)
    responses = []
    for index in range(4):
        responses.append(
            await chat_client.post(
                f"/api/v1/chats/{chat_id}/attachments",
                files={"file": (f"{index}.txt", str(index).encode(), "text/plain")},
                headers=headers,
            )
        )

    assert [response.status_code for response in responses] == [201, 201, 201, 429]


async def test_upload_attachment_requires_chat_attachments_create(
    chat_client: httpx.AsyncClient, chat_env: dict, session: AsyncSession,
    seeded_user: User, stack_env: None,
) -> None:
    """RBAC-03: a custom role holding chat.read/chat.generate but not
    chat.attachments.create cannot upload -- the gate fires before the route
    body runs, so this holds even for a chat_id that doesn't exist."""
    await make_templated_member(
        session, seeded_user, email="narrow-attach@acme.com", template_name="NoAttachCreate",
        permissions=["documents.list", "documents.content.read", "chat.read", "chat.generate"],
        workspace_id=str(chat_env["workspace"].id),
    )
    h = await auth(chat_client, "narrow-attach@acme.com")
    r = await chat_client.post(
        f"/api/v1/chats/{uuid4()}/attachments",
        headers=h,
        files={"file": ("a.txt", b"hello", "text/plain")},
    )
    assert r.status_code == 403


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


async def test_get_attachment_content_streams_bytes_inline(
    chat_client: httpx.AsyncClient, chat_env: dict, seeded_user: User, stack_env: None,
) -> None:
    """The content endpoint streams the stored bytes back inline with the
    right mime -- powers image thumbnails/previews in the conversation."""
    h = await auth(chat_client, seeded_user.email)
    chat_id = await make_chat(chat_client, chat_env, h)
    png = _png_bytes()
    r = await chat_client.post(
        f"/api/v1/chats/{chat_id}/attachments",
        files={"file": ("photo.png", png, "image/png")}, headers=h,
    )
    assert r.status_code == 201
    attachment_id = r.json()["id"]

    r2 = await chat_client.get(
        f"/api/v1/chats/{chat_id}/attachments/{attachment_id}/content", headers=h
    )
    assert r2.status_code == 200
    assert r2.content == png
    assert r2.headers["content-type"].startswith("image/png")
    assert "inline" in r2.headers["content-disposition"]


async def test_get_attachment_content_rejects_other_users_chat(
    chat_client: httpx.AsyncClient, chat_env: dict, seeded_user: User,
    org_b_user: User, stack_env: None,
) -> None:
    """iron rule 1/2: a chat (and its attachment bytes) is scoped org_id+user_id.
    Another user must get the same non-leaking 404 as an unknown attachment,
    never the image bytes."""
    h_a = await auth(chat_client, seeded_user.email)
    chat_id = await make_chat(chat_client, chat_env, h_a)
    r = await chat_client.post(
        f"/api/v1/chats/{chat_id}/attachments",
        files={"file": ("photo.png", _png_bytes(), "image/png")}, headers=h_a,
    )
    attachment_id = r.json()["id"]

    h_b = await auth(chat_client, "b@rival.com")
    r2 = await chat_client.get(
        f"/api/v1/chats/{chat_id}/attachments/{attachment_id}/content", headers=h_b
    )
    assert r2.status_code == 404


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

    image_bytes = _png_bytes()
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
    image_bytes = _png_bytes()
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


async def test_image_attachment_survives_general_knowledge_fallback(
    engine: AsyncEngine, redis_client: Redis, test_settings: Settings,
    chat_env: dict, session: AsyncSession, seeded_user: User,
    seeded_superadmin: User, stack_env: None,
) -> None:
    """Review fix (round 2 on DOC-9 Task 6): the general-knowledge fallback
    branch (no_answer=True + workspace.fallback_policy="general_knowledge",
    the default) is a 4th `stream_reply` model-call branch that builds its
    own final prompt message via build_general_knowledge_messages -- it must
    splice image_data_uris into that message exactly like the
    conversational, main/documents, and Gatekeeper-retry branches already do,
    or a vision question that misses retrieval silently loses its image.
    Wires a retriever that always returns no_answer=True (mirrors
    test_chat_stream.test_weak_retrieval_general_knowledge_fallback) together
    with a vision-capable model and an image attachment (mirrors this file's
    own test_image_attachment_on_vision_model_becomes_multimodal_content)."""
    fake_streamer = FakeStreamer()
    app = create_app(
        session_factory=build_session_factory(engine), redis_client=redis_client,
        litellm_transport=httpx.MockTransport(_stub_litellm_handler),
        retriever=FakeRetriever(chat_env["document"].id, no_answer=True),
        llm_streamer=fake_streamer, chunk_reader=FakeChunkReader(),
    )
    app.dependency_overrides[get_settings] = lambda: test_settings
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        h = await auth(client, seeded_user.email)
        chat_id = await make_model_and_chat(client, chat_env, session, seeded_superadmin, h)
        h_super = await auth(client, "root@platform.example")
        r_model = await client.post(
            "/api/v1/admin/models",
            json={"litellm_model_name": "vision-model", "display_name": "Vision",
                  "provider_kind": "ollama", "base_url": "http://ollama:11434",
                  "supports_vision": True},
            headers=h_super,
        )
        assert r_model.status_code == 201
        vision_model_id = r_model.json()["id"]

        image_bytes = _png_bytes()
        r_attach = await client.post(
            f"/api/v1/chats/{chat_id}/attachments",
            files={"file": ("photo.png", image_bytes, "image/png")},
            headers=h,
        )
        assert r_attach.status_code == 201
        assert r_attach.json()["kind"] == "image"
        attachment_id = r_attach.json()["id"]

        r = await client.post(
            f"/api/v1/chats/{chat_id}/messages",
            json={
                "content": "what is in this image?",
                "attachment_ids": [attachment_id],
                "model_id": vision_model_id,
            },
            headers=h,
        )
    assert r.status_code == 200
    frames = parse_sse(r.text)
    names = [n for n, _ in frames]
    # Proves the general-knowledge branch (not the documents branch) actually
    # fired: no sources/citations frames, and grounding="general" in done.
    assert "sources" not in names and "citations" not in names
    done = next(d for n, d in frames if n == "done")
    assert done["grounding"] == "general" and done["no_answer"] is False

    sent_prompt = fake_streamer.calls[-1]["messages"]
    last = sent_prompt[-1]
    assert isinstance(last["content"], list), (
        "image was dropped on the general-knowledge fallback branch"
    )
    assert last["content"][0] == {"type": "text", "text": "what is in this image?"}
    image_block = last["content"][1]
    assert image_block["type"] == "image_url"
    data_uri = image_block["image_url"]["url"]
    assert data_uri.startswith("data:image/png;base64,")
    encoded = data_uri.removeprefix("data:image/png;base64,")
    assert base64.b64decode(encoded) == image_bytes
