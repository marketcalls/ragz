import pytest
from qdrant_client import models
from sqlalchemy.ext.asyncio import AsyncSession

from raghub.core.errors import ConflictError, NotFoundError
from raghub.modules.documents.ingest import run_chunk, run_embed_upsert, run_parse
from raghub.modules.documents.metadata import (
    PRESET_FIELDS,
    build_clauses,
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


async def test_build_clauses_unknown_field_not_found(
    session: AsyncSession, stack_env: None
) -> None:
    ctx, ws = await seed_workspace(session, "meta9")
    with pytest.raises(NotFoundError):
        await build_clauses(session, ctx, ws.id, {"nonexistent_field": "x"})


async def test_build_clauses_prefixes_even_a_field_named_tenant_id(
    session: AsyncSession, stack_env: None
) -> None:
    """The meta. prefix is applied unconditionally — a workspace admin could
    literally create a field named 'tenant_id' (it matches the name pattern)
    and build_clauses must still emit key 'meta.tenant_id', never bare
    'tenant_id' (iron rule 1: user input can never address tenant keys)."""
    ctx, ws = await seed_workspace(session, "meta10")
    await create_field(
        session, ctx, ws.id, name="tenant_id", label="Tenant Id",
        field_type="text", options=None,
    )
    clauses = await build_clauses(session, ctx, ws.id, {"tenant_id": "evil-org"})
    assert len(clauses) == 1
    assert clauses[0].key == "meta.tenant_id"
    assert clauses[0].kind == "eq" and clauses[0].value == "evil-org"


async def test_build_clauses_eq_for_text_and_select(
    session: AsyncSession, stack_env: None
) -> None:
    ctx, ws = await seed_workspace(session, "meta11")
    await list_fields(session, ctx, ws.id)  # seed presets
    clauses = await build_clauses(
        session, ctx, ws.id, {"department": "HSE", "doc_type": "policy"}
    )
    by_key = {c.key: c for c in clauses}
    assert by_key["meta.department"].kind == "eq"
    assert by_key["meta.department"].value == "HSE"
    assert by_key["meta.doc_type"].kind == "eq"
    assert by_key["meta.doc_type"].value == "policy"


async def test_build_clauses_date_single_day(session: AsyncSession, stack_env: None) -> None:
    ctx, ws = await seed_workspace(session, "meta12")
    await list_fields(session, ctx, ws.id)  # seed presets incl revision_date
    clauses = await build_clauses(session, ctx, ws.id, {"revision_date": "2026-05-01"})
    assert len(clauses) == 1
    c = clauses[0]
    assert c.key == "meta.revision_date" and c.kind == "date_range"
    assert c.gte == "2026-05-01T00:00:00Z"
    assert c.lte == "2026-05-01T23:59:59Z"


async def test_build_clauses_date_range(session: AsyncSession, stack_env: None) -> None:
    ctx, ws = await seed_workspace(session, "meta13")
    await list_fields(session, ctx, ws.id)
    clauses = await build_clauses(
        session, ctx, ws.id, {"revision_date": "2026-01-01..2026-06-30"}
    )
    c = clauses[0]
    assert c.gte == "2026-01-01T00:00:00Z"
    assert c.lte == "2026-06-30T23:59:59Z"


async def test_build_clauses_date_range_open_ended(
    session: AsyncSession, stack_env: None
) -> None:
    ctx, ws = await seed_workspace(session, "meta14")
    await list_fields(session, ctx, ws.id)

    open_lower = await build_clauses(session, ctx, ws.id, {"revision_date": "..2026-06-30"})
    assert open_lower[0].gte is None
    assert open_lower[0].lte == "2026-06-30T23:59:59Z"

    open_upper = await build_clauses(session, ctx, ws.id, {"revision_date": "2026-01-01.."})
    assert open_upper[0].gte == "2026-01-01T00:00:00Z"
    assert open_upper[0].lte is None


async def test_build_clauses_rejects_malformed_date(session: AsyncSession) -> None:
    """User-typed date filters must fail as a typed 409, never survive into
    the Qdrant filter builder to die as a 500 (review round 1)."""
    ctx, ws = await seed_workspace(session, "metaBadDate")
    await list_fields(session, ctx, ws.id)  # seed presets (revision_date)
    with pytest.raises(ConflictError):
        await build_clauses(session, ctx, ws.id, {"revision_date": "not-a-date"})
    with pytest.raises(ConflictError):
        await build_clauses(session, ctx, ws.id, {"revision_date": "2026-01-01..nope"})
