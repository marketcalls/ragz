"""Ephemeral chat-attachment text extraction. Reuses the SAME Docling
primitive the permanent-document pipeline uses (`parse_bytes`) — no
separate OCR code path exists or is needed. Docling's default image
pipeline (InputFormat.IMAGE) already runs OCR (do_ocr=True by default),
so a photo/screenshot attachment extracts text through the identical call
as a text document; no branching on `kind` happens here."""

from collections import defaultdict
from dataclasses import dataclass
from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ragz.core.config import get_settings
from ragz.core.errors import NotFoundError
from ragz.core.storage import build_storage
from ragz.modules.chat.chats import get_chat
from ragz.modules.chat.models import ChatAttachment
from ragz.modules.chat.prompting import PromptSource, count_tokens
from ragz.modules.documents.pipeline import PageBlock, chunk_blocks, embed_batch, parse_bytes
from ragz.modules.models import service as models_service
from ragz.modules.models.models import LOCAL_EMBEDDING_MODEL_ID
from ragz.modules.retrieval.embeddings import get_dense_embedder
from ragz.modules.retrieval.service import (
    ensure_ephemeral_collection,
    upsert_ephemeral_chunks,
)
from ragz.modules.tenancy.context import TenantContext


def extract_text(data: bytes, filename: str) -> str:
    """Best-effort text extraction for a chat attachment (document or
    image). Returns "" on any parse failure rather than raising — a failed
    extraction degrades to "no inline/retrieval content available" for this
    attachment, never blocks the chat."""
    try:
        blocks = parse_bytes(data, filename)
    except Exception:
        return ""
    return "\n\n".join(b.text for b in blocks)

# --- attachment lifecycle -------------------------------------------------
# Moved here from chat/service.py (Phase 2 item 2). This file already owned
# extract_text; the CRUD, storage and ephemeral-indexing half of the same
# concept lived 1500 lines away in service.py. get_chat is imported from
# chat.chats, which is why that had to be extracted first.

_ATTACHMENT_KINDS = {
    "text/plain", "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


def _attachment_kind(mime: str) -> str:
    return "image" if mime.startswith("image/") else "document"


async def create_attachment(
    session: AsyncSession, ctx: TenantContext, chat_id: UUID,
    *, filename: str, mime: str, data: bytes,
) -> ChatAttachment:
    await get_chat(session, ctx, chat_id)  # NotFoundError if not the caller's chat
    kind = _attachment_kind(mime)
    attachment = ChatAttachment(
        chat_id=chat_id, kind=kind, filename=filename, mime=mime, storage_key="",
    )
    session.add(attachment)
    await session.flush()  # assigns attachment.id for the storage key
    attachment.storage_key = f"{ctx.org_id}/chats/{chat_id}/{attachment.id}/{filename}"
    storage = build_storage(get_settings())
    await storage.ensure_bucket()
    await storage.put(attachment.storage_key, data, content_type=mime)
    await session.commit()
    return attachment


async def get_attachment_for_chat(
    session: AsyncSession, ctx: TenantContext, chat_id: UUID, attachment_id: UUID
) -> ChatAttachment:
    """Load a chat attachment for content read, gated by chat ownership.

    get_chat enforces org_id + user_id (a chat belongs to exactly one user),
    so this is not cross-user readable. The attachment must also belong to
    THIS chat -- an attachment_id from another (even same-user) chat is a
    non-leaking NotFound, same as an unknown id. Mirrors the documents
    file-read gate (get_document_checked) in intent."""
    await get_chat(session, ctx, chat_id)  # NotFoundError if not the caller's chat
    attachment = await session.get(ChatAttachment, attachment_id)
    if attachment is None or attachment.chat_id != chat_id:
        raise NotFoundError("attachment not found")
    return attachment


@dataclass(frozen=True, slots=True)
class AttachmentExtractionView:
    """The three fields the extraction worker reads off an attachment.

    Same reasoning as tenancy's WorkspaceView: the worker is an entrypoint, and
    entrypoints call module services rather than query another module's ORM
    (Phase 2 item 1). It used to `session.get(ChatAttachment, ...)` itself,
    which coupled it to chat's schema and handed it a live, mutable, lazily
    loading row it only ever read three scalars from.
    """

    id: UUID
    storage_key: str
    filename: str


async def get_attachment_for_extraction(
    session: AsyncSession, attachment_id: UUID
) -> AttachmentExtractionView | None:
    """Load the fields the extraction task needs, or None if the row is gone.

    Unchecked by design: the task runs behind an authenticated upload that
    already authorized this attachment, and it holds no TenantContext. None
    (rather than NotFoundError) because a deleted attachment is a normal race
    for a queued task, not an error worth retrying.
    """
    attachment = await session.get(ChatAttachment, attachment_id)
    if attachment is None:
        return None
    return AttachmentExtractionView(
        id=attachment.id,
        storage_key=attachment.storage_key,
        filename=attachment.filename,
    )


async def mark_attachment_processing(session: AsyncSession, attachment_id: UUID) -> None:
    attachment = await session.get(ChatAttachment, attachment_id)
    if attachment is not None:
        attachment.status = "processing"
        await session.commit()


async def mark_attachment_ready(
    session: AsyncSession, attachment_id: UUID, extracted_text: str
) -> None:
    attachment = await session.get(ChatAttachment, attachment_id)
    if attachment is not None:
        attachment.extracted_text = extracted_text
        attachment.status = "ready"
        await session.commit()


async def mark_attachment_failed(session: AsyncSession, attachment_id: UUID) -> None:
    attachment = await session.get(ChatAttachment, attachment_id)
    if attachment is not None:
        attachment.status = "failed"
        await session.commit()


async def list_stale_attachments(
    session: AsyncSession, cutoff: datetime
) -> list[ChatAttachment]:
    """Task 7 (DOC-9): attachments older than the 24h TTL, for the daily Beat
    sweep. Deletion itself (DB row + MinIO blob + Qdrant points) is the
    caller's job (worker/tasks.py's cleanup_stale_attachments_task) so each
    side-effect stays independently testable."""
    stmt = select(ChatAttachment).where(ChatAttachment.created_at < cutoff)
    return list((await session.execute(stmt)).scalars())


async def delete_attachment(session: AsyncSession, attachment: ChatAttachment) -> None:
    """Deletes the DB row only -- MinIO blob and Qdrant points are the
    caller's responsibility (see list_stale_attachments)."""
    await session.delete(attachment)
    await session.commit()


async def link_attachments_to_message(
    session: AsyncSession, attachments: Sequence[ChatAttachment], message_id: UUID
) -> None:
    """Transcript rendering: stamp this turn's attachments with the user
    Message they were actually sent on. Called from chats.py::send_message
    AFTER add_user_message persists the message -- attachment resolution
    happens first (fail-fast, same convention as the quota/model checks),
    so the message id isn't known until now."""
    for attachment in attachments:
        attachment.message_id = message_id
    await session.commit()


async def list_attachments_by_message(
    session: AsyncSession, chat_id: UUID
) -> dict[UUID, list[ChatAttachment]]:
    stmt = (
        select(ChatAttachment)
        .where(ChatAttachment.chat_id == chat_id, ChatAttachment.message_id.isnot(None))
        .order_by(ChatAttachment.created_at)
    )
    by_message: dict[UUID, list[ChatAttachment]] = defaultdict(list)
    for attachment in (await session.execute(stmt)).scalars():
        by_message[attachment.message_id].append(attachment)  # type: ignore[index]
    return by_message


_ATTACHMENT_INLINE_TOKEN_BUDGET = 4000


async def route_attachment(
    session: AsyncSession, org_id: UUID, chat_id: UUID,
    attachment: ChatAttachment, marker: int, model_hint: str | None,
) -> "PromptSource | None":
    """Inline if the attachment's extracted text fits the budget; otherwise
    chunk+embed+upsert into the ephemeral collection and return None (the
    caller's existing retrieval call picks it up via search_ephemeral_attachments,
    merged like any other candidate chunk group)."""
    text = attachment.extracted_text or ""
    if not text.strip():
        return None
    if count_tokens(text, model_hint) <= _ATTACHMENT_INLINE_TOKEN_BUDGET:
        attachment.routed_to = "inline"
        await session.commit()
        return PromptSource(marker=marker, filename=attachment.filename, page=1, text=text)

    attachment.routed_to = "retrieval"
    await session.commit()
    chunks = chunk_blocks(
        [PageBlock(page=1, text=text, kind="text")]
    )
    # DOC-10: the ephemeral attachments store has no per-workspace embedding
    # choice (ensure_ephemeral_collection's own docstring) -- always the
    # seeded local model, never the calling workspace's embedding_model_id.
    ephemeral_model = await models_service.get_model(session, LOCAL_EMBEDDING_MODEL_ID)
    dense_embedder = get_dense_embedder(
        ephemeral_model.id, provider_kind=ephemeral_model.provider_kind,
        litellm_model_name=ephemeral_model.litellm_model_name,
    )
    dense, sparse = await embed_batch([c.text for c in chunks], dense_embedder)
    await ensure_ephemeral_collection()
    await upsert_ephemeral_chunks(
        org_id=org_id, chat_id=chat_id, attachment_id=attachment.id,
        chunks=chunks, dense=dense, sparse=sparse,
    )
    return None

