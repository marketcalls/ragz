import pytest
from qdrant_client import models
from sqlalchemy.ext.asyncio import AsyncSession

from raghub.core.errors import ConflictError, NotFoundError
from raghub.modules.documents.ingest import run_chunk, run_embed_upsert, run_parse
from raghub.modules.documents.metadata import (
    PRESET_FIELDS,
    create_field,
    list_fields,
    set_document_metadata,
)
from raghub.modules.documents.service import create_from_upload
from raghub.modules.retrieval.client import COLLECTION, get_qdrant
from tests.modules.retrieval.test_retrieve import seed_workspace


async def _index(session, ctx, ws, filename, text):  # type: ignore[no-untyped-def]
    """Fully ingest a document (parse -> chunk -> embed/upsert) so its points
    actually land in Qdrant, non-vacuously exercising the payload mirror."""
    doc = await create_from_upload(
        session, ctx, ws.id, filename=filename, mime="text/plain", data=text.encode()
    )
    await run_parse(doc.id)
    await run_chunk(doc.id)
    await run_embed_upsert(doc.id)
    await session.refresh(doc)
    return doc


async def test_list_fields_lazily_seeds_presets_once(
    session: AsyncSession, stack_env: None
) -> None:
    ctx, ws = await seed_workspace(session, "meta1")
    fields = await list_fields(session, ctx, ws.id)
    assert [f.name for f in fields] == [p["name"] for p in PRESET_FIELDS]

    again = await list_fields(session, ctx, ws.id)
    assert [f.id for f in again] == [f.id for f in fields]  # no duplication


async def test_create_select_field_without_options_conflicts(
    session: AsyncSession, stack_env: None
) -> None:
    ctx, ws = await seed_workspace(session, "meta2")
    with pytest.raises(ConflictError):
        await create_field(
            session, ctx, ws.id, name="custom_select", label="Custom",
            field_type="select", options=None,
        )


async def test_create_field_rejects_bad_name(session: AsyncSession, stack_env: None) -> None:
    ctx, ws = await seed_workspace(session, "meta3")
    with pytest.raises(ConflictError):
        await create_field(
            session, ctx, ws.id, name="Bad Name!", label="Bad", field_type="text", options=None,
        )


async def test_set_document_metadata_stores_and_mirrors_to_qdrant(
    session: AsyncSession, qdrant_collection: None
) -> None:
    ctx, ws = await seed_workspace(session, "meta4")
    await list_fields(session, ctx, ws.id)  # seed presets
    doc = await _index(session, ctx, ws, "policy.txt", "the fire escape plan is on file")

    updated = await set_document_metadata(
        session, ctx, doc.id,
        {"department": "safety", "doc_type": "policy", "revision_date": "2026-05-01"},
    )
    assert updated.meta == {
        "department": "safety", "doc_type": "policy", "revision_date": "2026-05-01",
    }

    points, _ = await get_qdrant().scroll(
        COLLECTION,
        scroll_filter=models.Filter(
            must=[
                models.FieldCondition(
                    key="document_id", match=models.MatchValue(value=str(doc.id))
                )
            ]
        ),
        limit=100,
        with_payload=True,
    )
    assert points  # non-vacuous: the document actually indexed something
    for point in points:
        payload = point.payload or {}
        assert payload.get("meta") == {
            "department": "safety", "doc_type": "policy", "revision_date": "2026-05-01",
        }


async def test_set_document_metadata_unknown_key_not_found(
    session: AsyncSession, stack_env: None
) -> None:
    ctx, ws = await seed_workspace(session, "meta5")
    await list_fields(session, ctx, ws.id)  # seed presets
    doc = await create_from_upload(
        session, ctx, ws.id, filename="a.txt", mime="text/plain", data=b"x"
    )
    with pytest.raises(NotFoundError):
        await set_document_metadata(session, ctx, doc.id, {"nonexistent_field": "x"})


async def test_set_document_metadata_select_value_outside_options_conflicts(
    session: AsyncSession, stack_env: None
) -> None:
    ctx, ws = await seed_workspace(session, "meta6")
    await list_fields(session, ctx, ws.id)  # seed presets
    doc = await create_from_upload(
        session, ctx, ws.id, filename="a.txt", mime="text/plain", data=b"x"
    )
    with pytest.raises(ConflictError):
        await set_document_metadata(session, ctx, doc.id, {"doc_type": "spreadsheet"})


async def test_set_document_metadata_bad_date_conflicts(
    session: AsyncSession, stack_env: None
) -> None:
    ctx, ws = await seed_workspace(session, "meta7")
    await list_fields(session, ctx, ws.id)  # seed presets
    doc = await create_from_upload(
        session, ctx, ws.id, filename="a.txt", mime="text/plain", data=b"x"
    )
    with pytest.raises(ConflictError):
        await set_document_metadata(session, ctx, doc.id, {"revision_date": "not-a-date"})


async def test_set_document_metadata_valid_date_accepted(
    session: AsyncSession, stack_env: None
) -> None:
    ctx, ws = await seed_workspace(session, "meta8")
    await list_fields(session, ctx, ws.id)  # seed presets
    doc = await create_from_upload(
        session, ctx, ws.id, filename="a.txt", mime="text/plain", data=b"x"
    )
    updated = await set_document_metadata(session, ctx, doc.id, {"revision_date": "2026-05-01"})
    assert updated.meta == {"revision_date": "2026-05-01"}
