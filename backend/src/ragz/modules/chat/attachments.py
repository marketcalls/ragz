"""Ephemeral chat-attachment text extraction. Reuses the SAME Docling
primitive the permanent-document pipeline uses (`parse_bytes`) — no
separate OCR code path exists or is needed. Docling's default image
pipeline (InputFormat.IMAGE) already runs OCR (do_ocr=True by default),
so a photo/screenshot attachment extracts text through the identical call
as a text document; no branching on `kind` happens here."""

import warnings
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import BinaryIO
from uuid import UUID
from zipfile import BadZipFile, ZipFile

import structlog
from PIL import Image
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ragz.core.config import Settings
from ragz.core.db import committed_row_exists_after_error
from ragz.core.errors import (
    NotFoundError,
    OrgResourceQuotaExceeded,
    PayloadTooLarge,
    UnsupportedMediaType,
)
from ragz.core.storage import build_storage
from ragz.modules.chat.chats import get_chat
from ragz.modules.chat.cleanup import schedule_cleanup
from ragz.modules.chat.models import AttachmentCleanupJob, Chat, ChatAttachment
from ragz.modules.chat.prompting import PromptSource, count_tokens
from ragz.modules.documents.pipeline import PageBlock, chunk_blocks, embed_batch, parse_bytes
from ragz.modules.documents.uploads import UploadedContent
from ragz.modules.models import service as models_service
from ragz.modules.models.models import LOCAL_EMBEDDING_MODEL_ID
from ragz.modules.quotas import resource_admission
from ragz.modules.retrieval.embeddings import get_dense_embedder
from ragz.modules.retrieval.service import (
    ensure_ephemeral_collection,
    upsert_ephemeral_chunks,
)
from ragz.modules.tenancy.context import TenantContext

log = structlog.get_logger()


class AttachmentParserLimitExceeded(Exception):
    pass


def extract_text(
    data: bytes,
    filename: str,
    *,
    max_pages: int | None = None,
    max_chars: int | None = None,
) -> str:
    """Best-effort text extraction for a chat attachment (document or
    image). Returns "" on any parse failure rather than raising — a failed
    extraction degrades to "no inline/retrieval content available" for this
    attachment, never blocks the chat."""
    try:
        blocks = parse_bytes(data, filename)
    except Exception:
        return ""
    if max_pages is not None and len({block.page for block in blocks}) > max_pages:
        raise AttachmentParserLimitExceeded("attachment exceeds parser page limit")
    extracted_chars = sum(len(block.text) for block in blocks)
    if max_chars is not None and extracted_chars > max_chars:
        raise AttachmentParserLimitExceeded("attachment exceeds extracted text limit")
    return "\n\n".join(b.text for b in blocks)

# --- attachment lifecycle -------------------------------------------------
# Moved here from chat/service.py (Phase 2 item 2). This file already owned
# extract_text; the CRUD, storage and ephemeral-indexing half of the same
# concept lived 1500 lines away in service.py. get_chat is imported from
# chat.chats, which is why that had to be extracted first.

_ATTACHMENT_KINDS = {
    "text/plain", "text/markdown", "text/csv", "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "image/png", "image/jpeg", "image/gif", "image/webp", "image/bmp", "image/tiff",
}


def _attachment_kind(mime: str) -> str:
    return "image" if mime.startswith("image/") else "document"


def validate_attachment_mime(mime: str) -> None:
    if mime not in _ATTACHMENT_KINDS:
        raise UnsupportedMediaType("unsupported chat attachment type")


def validate_attachment_parser_resources(
    stream: BinaryIO, filename: str, settings: Settings
) -> None:
    """Reject predictable archive/PDF work bombs before storage or enqueue."""

    suffix = Path(filename).suffix.lower()
    position = stream.tell()
    try:
        stream.seek(0)
        if suffix in {".docx", ".xlsx", ".pptx"}:
            try:
                with ZipFile(stream) as archive:
                    entries = archive.infolist()
                    if len(entries) > settings.attachment_max_archive_entries:
                        raise PayloadTooLarge("attachment archive has too many entries")
                    if (
                        sum(entry.file_size for entry in entries)
                        > settings.attachment_max_uncompressed_bytes
                    ):
                        raise PayloadTooLarge("attachment expands beyond the parser byte limit")
            except BadZipFile as exc:
                raise UnsupportedMediaType("invalid Office Open XML attachment") from exc
        elif suffix == ".pdf":
            try:
                from pypdfium2 import PdfDocument  # type: ignore[import-untyped]

                pdf = PdfDocument(stream)
                try:
                    if len(pdf) > settings.attachment_max_pages:
                        raise PayloadTooLarge("attachment exceeds parser page limit")
                finally:
                    pdf.close()
            except PayloadTooLarge:
                raise
            except Exception as exc:
                raise UnsupportedMediaType("invalid PDF attachment") from exc
        elif suffix in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff"}:
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("error", Image.DecompressionBombWarning)
                    with Image.open(stream) as image:
                        if image.width * image.height > settings.attachment_max_image_pixels:
                            raise PayloadTooLarge(
                                "attachment image exceeds the decoded pixel limit"
                            )
                        image.verify()
            except PayloadTooLarge:
                raise
            except (Image.DecompressionBombWarning, Image.DecompressionBombError) as exc:
                raise PayloadTooLarge(
                    "attachment image exceeds the decoded pixel limit"
                ) from exc
            except Exception as exc:
                raise UnsupportedMediaType("invalid image attachment") from exc
    finally:
        stream.seek(position)


async def _attachment_usage(
    session: AsyncSession, *, org_id: UUID, user_id: UUID | None
) -> tuple[int, int, int]:
    conditions = [Chat.org_id == org_id]
    if user_id is not None:
        conditions.append(Chat.user_id == user_id)
    count, size_bytes, pending = (
        await session.execute(
            select(
                func.count(ChatAttachment.id),
                func.coalesce(func.sum(ChatAttachment.size_bytes), 0),
                func.count(ChatAttachment.id).filter(
                    ChatAttachment.status.in_(("queued", "processing"))
                ),
            )
            .join(Chat, Chat.id == ChatAttachment.chat_id)
            .where(*conditions)
        )
    ).one()
    cleanup_conditions = [
        AttachmentCleanupJob.org_id == org_id,
        AttachmentCleanupJob.completed_at.is_(None),
    ]
    if user_id is not None:
        cleanup_conditions.append(AttachmentCleanupJob.user_id == user_id)
    cleanup_count, cleanup_bytes = (
        await session.execute(
            select(
                func.count(AttachmentCleanupJob.id),
                func.coalesce(func.sum(AttachmentCleanupJob.size_bytes), 0),
            ).where(*cleanup_conditions)
        )
    ).one()
    return (
        int(count) + int(cleanup_count),
        int(size_bytes) + int(cleanup_bytes),
        int(pending),
    )


async def _reserve_attachment(
    session: AsyncSession,
    ctx: TenantContext,
    *,
    size_bytes: int,
    settings: Settings,
) -> UUID:
    await resource_admission.lock_org(session, ctx.org_id)
    await resource_admission.prune_expired(session)
    org_count, org_bytes, org_pending = await _attachment_usage(
        session, org_id=ctx.org_id, user_id=None
    )
    user_count, user_bytes, user_pending = await _attachment_usage(
        session, org_id=ctx.org_id, user_id=ctx.user_id
    )
    org_reserved = await resource_admission.totals(
        session, org_id=ctx.org_id, kind="attachment"
    )
    user_reserved = await resource_admission.totals(
        session, org_id=ctx.org_id, user_id=ctx.user_id, kind="attachment"
    )

    checks = (
        (
            org_count + org_reserved.count >= settings.attachment_max_count_per_org,
            "organization attachment count limit reached",
        ),
        (
            user_count + user_reserved.count >= settings.attachment_max_count_per_user,
            "user attachment count limit reached",
        ),
        (
            org_bytes + org_reserved.size_bytes + size_bytes
            > settings.attachment_max_bytes_per_org,
            "organization attachment storage limit reached",
        ),
        (
            user_bytes + user_reserved.size_bytes + size_bytes
            > settings.attachment_max_bytes_per_user,
            "user attachment storage limit reached",
        ),
        (
            org_pending + org_reserved.count >= settings.attachment_max_pending_per_org,
            "organization pending attachment limit reached",
        ),
        (
            user_pending + user_reserved.count >= settings.attachment_max_pending_per_user,
            "user pending attachment limit reached",
        ),
    )
    for exceeded, detail in checks:
        if exceeded:
            raise OrgResourceQuotaExceeded(detail)

    reservation = resource_admission.add(
        session,
        org_id=ctx.org_id,
        user_id=ctx.user_id,
        kind="attachment",
        size_bytes=size_bytes,
    )
    await session.commit()
    return reservation.id


async def create_attachment(
    session: AsyncSession, ctx: TenantContext, chat_id: UUID,
    *, filename: str, mime: str, data: bytes | UploadedContent, settings: Settings,
) -> ChatAttachment:
    await get_chat(session, ctx, chat_id)  # NotFoundError if not the caller's chat
    validate_attachment_mime(mime)
    content = data if isinstance(data, UploadedContent) else UploadedContent.from_bytes(data)
    reservation_id = await _reserve_attachment(
        session, ctx, size_bytes=content.size_bytes, settings=settings
    )
    kind = _attachment_kind(mime)
    attachment = ChatAttachment(
        chat_id=chat_id, kind=kind, filename=filename, mime=mime, storage_key="",
        size_bytes=content.size_bytes,
    )
    storage = build_storage(settings)
    try:
        session.add(attachment)
        await session.flush()
        attachment.storage_key = f"{ctx.org_id}/chats/{chat_id}/{attachment.id}/{filename}"
        await storage.ensure_bucket()
        await storage.put_stream(attachment.storage_key, content.stream, content_type=mime)
        await resource_admission.remove(session, reservation_id)
    except BaseException:
        await session.rollback()
        # Delete unconditionally. A cancelled multipart helper can raise after
        # the object store accepted its completion response, so a local
        # `stored` flag is not authoritative. S3 deletion is idempotent.
        try:
            await storage.delete(attachment.storage_key)
        except Exception:
            log.exception(
                "attachment_upload_compensation_failed",
                attachment_id=str(attachment.id),
                storage_key=attachment.storage_key,
            )
        await resource_admission.release(session, reservation_id)
        raise
    try:
        await session.commit()
    except BaseException:
        persisted = await committed_row_exists_after_error(
            session, select(ChatAttachment.id).where(ChatAttachment.id == attachment.id)
        )
        if persisted is False:
            try:
                await storage.delete(attachment.storage_key)
            except Exception:
                log.exception(
                    "attachment_upload_compensation_failed",
                    attachment_id=str(attachment.id),
                    storage_key=attachment.storage_key,
                )
            await resource_admission.release(session, reservation_id)
        elif persisted is None:
            log.error(
                "attachment_upload_commit_outcome_unknown",
                attachment_id=str(attachment.id),
                storage_key=attachment.storage_key,
            )
        raise
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
    """Schedule durable external cleanup before deleting the attachment row."""
    chat = await session.get(Chat, attachment.chat_id)
    if chat is not None:
        await schedule_cleanup(
            session,
            attachment,
            org_id=chat.org_id,
            user_id=chat.user_id,
        )
        await session.flush()
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
