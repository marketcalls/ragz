import asyncio
import os

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from structlog.testing import capture_logs

from ragz.modules.auth.models import User
from ragz.modules.chat.models import Chat, ChatAttachment
from ragz.modules.tenancy.models import Organization, Workspace
from ragz.worker import tasks


def test_extract_text_from_plain_document(monkeypatch) -> None:
    from ragz.modules.chat.attachments import extract_text
    text = extract_text(b"Hello attachment world", "notes.txt")
    assert "Hello attachment world" in text


@pytest.mark.skipif(
    not os.environ.get("RAGZ_TEST_OCR"), reason="set RAGZ_TEST_OCR=1 to run OCR e2e"
)
def test_extract_text_from_image_runs_ocr_by_default(tmp_path) -> None:
    # Uses a real tiny PNG with rendered text — generate with PIL in the test
    # fixture setup, or use a checked-in tiny fixture PNG under tests/fixtures/
    # if one already exists in this repo (check first). Docling's default
    # image pipeline has do_ocr=True (confirmed at plan-writing time), so this
    # is a real OCR run, not a mock.
    import io

    from PIL import Image, ImageDraw

    from ragz.modules.chat.attachments import extract_text
    img = Image.new("RGB", (300, 80), color="white")
    draw = ImageDraw.Draw(img)
    draw.text((10, 10), "ATTACHMENT TEST", fill="black")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    text = extract_text(buf.getvalue(), "photo.png")
    assert "ATTACHMENT" in text.upper()


async def test_process_attachment_task_outer_failure_is_caught_and_logged(
    session: AsyncSession, stack_env: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Review fix regression (DOC-9 Task 2 finding): a failure OUTSIDE the
    existing inner extraction try/except -- e.g. mark_attachment_processing's
    own commit raising a transient DB error -- must not escape the Celery
    task, and must still flip the attachment to "failed" on a best-effort
    basis, matching audit_message_task's outer-guard convention."""
    org = Organization(name="AttachOuterFailOrg")
    session.add(org)
    await session.flush()
    user = User(
        org_id=org.id, email="outerfail@attachorg.com", password_hash="x",  # noqa: S106
        role="admin",
    )
    ws = Workspace(org_id=org.id, name="ws")
    session.add_all([user, ws])
    await session.flush()
    chat = Chat(org_id=org.id, workspace_id=ws.id, user_id=user.id)
    session.add(chat)
    await session.flush()
    attachment = ChatAttachment(
        chat_id=chat.id, kind="document", filename="notes.txt",
        mime="text/plain", storage_key="unused",
    )
    session.add(attachment)
    await session.commit()

    async def _boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("simulated transient DB error")

    # Patches a call OUTSIDE the inner try/except (which only wraps
    # storage.get/extract_text/mark_attachment_ready) -- this is the gap the
    # review finding identified.
    monkeypatch.setattr(tasks.chat_service, "mark_attachment_processing", _boom)

    with capture_logs() as cap:
        # asyncio.to_thread: process_attachment_task calls asyncio.run()
        # internally, which cannot nest inside this test's own running loop
        # (same pattern as test_run_all_workspaces_enqueues_only_... in
        # tests/worker/test_tasks.py).
        await asyncio.to_thread(tasks.process_attachment_task, str(attachment.id))

    events = [entry["event"] for entry in cap]
    assert "attachment_processing_outer_failed" in events

    await session.refresh(attachment)
    assert attachment.status == "failed"
