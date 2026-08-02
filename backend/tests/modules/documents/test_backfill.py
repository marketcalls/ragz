"""Toggle-ON backfill (spec §4): flipping enrichment_enabled enriches
existing current-version documents via a background job, attributed to
ingestion quota. Reuses IngestJob rows (stage="enrich") - no new table
(Plan K's explicit migration-arithmetic decision)."""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ragz.modules.documents.ingest import (
    run_enrichment_backfill,
    run_enrichment_backfill_for_workspace,
)
from ragz.modules.documents.models import Document, IngestJob
from ragz.modules.documents.service import create_from_upload
from ragz.modules.models.models import Model
from ragz.modules.quotas.models import UsageRecord
from tests.modules.documents.test_ingest import (
    _ENRICH_RESPONSE,
    _patch_utility_completer,
    _upload_with_chunks,
)


async def _mark_indexed_and_current(session: AsyncSession, doc: Document, *,
                                    is_current: bool = True) -> None:
    """Test-only shortcut past run_embed_upsert: these tests exercise
    run_enrichment_backfill's own qdrant/embedding work directly, so the doc
    only needs to already look "indexed" in Postgres — it must not have gone
    through run_embed_upsert already, which (with a utility model present)
    would enrich it immediately and defeat the "not yet enriched" setup."""
    doc.status = "indexed"
    doc.is_current = is_current
    await session.commit()


async def test_workspace_backfill_selects_current_indexed_unenriched_and_skips_superseded(
    session: AsyncSession, qdrant_collection: None, utility_model: Model,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_utility_completer(monkeypatch, _ENRICH_RESPONSE)
    ctx, ws, current_doc = await _upload_with_chunks(session, "bf1", enrichment_enabled=True)
    await _mark_indexed_and_current(session, current_doc, is_current=True)

    superseded_doc = await create_from_upload(
        session, ctx, ws.id, filename="superseded.txt", mime="text/plain", data=b"old version"
    )
    await _mark_indexed_and_current(session, superseded_doc, is_current=False)

    await run_enrichment_backfill_for_workspace(ws.id)

    current_jobs = (
        await session.execute(
            select(IngestJob).where(IngestJob.document_id == current_doc.id)
        )
    ).scalars().all()
    assert any(j.stage == "enrich" for j in current_jobs)

    superseded_jobs = (
        await session.execute(
            select(IngestJob).where(IngestJob.document_id == superseded_doc.id)
        )
    ).scalars().all()
    assert not superseded_jobs

    await session.refresh(current_doc)
    assert current_doc.enriched is True


async def test_workspace_backfill_is_noop_when_already_enriched(
    session: AsyncSession, qdrant_collection: None, utility_model: Model,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_utility_completer(monkeypatch, _ENRICH_RESPONSE)
    _ctx, ws, doc = await _upload_with_chunks(session, "bf2", enrichment_enabled=True)
    await _mark_indexed_and_current(session, doc, is_current=True)
    doc.enriched = True
    await session.commit()

    await run_enrichment_backfill_for_workspace(ws.id)

    jobs = (
        await session.execute(select(IngestJob).where(IngestJob.document_id == doc.id))
    ).scalars().all()
    assert not any(j.stage == "enrich" for j in jobs)


async def test_run_enrichment_backfill_marks_document_enriched_and_meters_ingestion(
    session: AsyncSession, qdrant_collection: None, utility_model: Model,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_utility_completer(monkeypatch, _ENRICH_RESPONSE)
    _ctx, _ws, doc = await _upload_with_chunks(session, "bf3", enrichment_enabled=True)
    await _mark_indexed_and_current(session, doc, is_current=True)

    await run_enrichment_backfill(doc.id)

    await session.refresh(doc)
    assert doc.enriched is True
    records = (
        await session.execute(select(UsageRecord).where(UsageRecord.org_id == doc.org_id))
    ).scalars().all()
    assert any(r.feature == "ingestion" for r in records)


async def test_run_enrichment_backfill_noop_if_already_enriched(
    session: AsyncSession, qdrant_collection: None, utility_model: Model,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _patch_utility_completer(monkeypatch, _ENRICH_RESPONSE)
    _ctx, _ws, doc = await _upload_with_chunks(session, "bf4", enrichment_enabled=True)
    await _mark_indexed_and_current(session, doc, is_current=True)
    doc.enriched = True
    await session.commit()

    await run_enrichment_backfill(doc.id)

    assert fake.calls == 0  # never even reached the utility model


async def test_run_enrichment_backfill_noop_if_workspace_toggled_back_off(
    session: AsyncSession, qdrant_collection: None, utility_model: Model,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Guards against the enqueue/execution race: a workspace flipped back
    OFF after a backfill was enqueued but before the task ran must not
    silently enrich anyway."""
    fake = _patch_utility_completer(monkeypatch, _ENRICH_RESPONSE)
    _ctx, ws, doc = await _upload_with_chunks(session, "bf5", enrichment_enabled=True)
    await _mark_indexed_and_current(session, doc, is_current=True)
    ws.enrichment_enabled = False
    await session.commit()

    await run_enrichment_backfill(doc.id)

    await session.refresh(doc)
    assert doc.enriched is False
    assert fake.calls == 0
