"""Evasion probes (Phase 3 §6/§8): a user tries to read a document they
cannot open, a superseded version's content, or filter-inject a bare
Qdrant payload key through the metadata search_by_metadata tool.

Gated behind REDTEAM=1 - see tests/redteam/conftest.py.
"""

import pytest

from ragz.core.errors import NotFoundError
from ragz.modules.documents.metadata import build_clauses, create_field
from ragz.modules.retrieval.client import COLLECTION
from ragz.modules.retrieval.service import MetadataClause, retrieve, update_document_acl
from tests.isolation.conftest import ingest_text, seed_acl_workspace

from .conftest import REDTEAM_ENABLED

pytestmark = pytest.mark.skipif(
    not REDTEAM_ENABLED, reason="set REDTEAM=1 to run the red-team tier"
)


async def test_outsider_cannot_retrieve_acl_restricted_document(session, qdrant_collection) -> None:  # type: ignore[no-untyped-def]
    """A user outside the document's ACL group must never see its content in
    retrieval results (iron rule 2: ACL is enforced inside the vector query,
    never post-filtered). The insider check below is the non-vacuous half -
    proof the document really is findable, just not by the outsider."""
    ctx_in, ctx_out, ctx_admin, ws, finance = await seed_acl_workspace(session)
    doc = await ingest_text(session, ctx_in, ws, "finance.txt", "Q3 revenue is confidential: $42M.")
    await update_document_acl(
        ws.org_id, doc.id, acl_group_ids=[finance.id], collection_name=COLLECTION
    )

    outsider_result = await retrieve(session, ctx_out, ws.id, "Q3 revenue confidential")
    assert not any("42M" in c.text for c in outsider_result.chunks)

    insider_result = await retrieve(session, ctx_in, ws.id, "Q3 revenue confidential")
    assert any("42M" in c.text for c in insider_result.chunks)  # non-vacuous


async def test_metadata_filter_cannot_target_tenant_key(session, redteam_env) -> None:  # type: ignore[no-untyped-def]
    """`build_clauses` (documents/metadata.py) unconditionally prefixes every
    clause key with "meta." regardless of the field's own name (DOC-6), so a
    caller can never address a bare Qdrant payload key like tenant_id/
    workspace_id/acl_groups/is_current through the metadata filter surface -
    those names simply never resolve as metadata field names. The trailing
    real-field call is the non-vacuous half: build_clauses is NOT broken for
    everything, and it always emits the meta.-prefixed key for a legitimate
    field."""
    ctx, ws = redteam_env
    await create_field(
        session, ctx, ws.id, name="department", label="Department",
        field_type="text", options=None,
    )
    for hostile_key in ("tenant_id", "workspace_id", "acl_groups", "is_current"):
        with pytest.raises(NotFoundError):
            await build_clauses(session, ctx, ws.id, {hostile_key: "anything"})

    clauses = await build_clauses(session, ctx, ws.id, {"department": "finance"})
    assert clauses == [MetadataClause(key="meta.department", kind="eq", value="finance")]


async def test_superseded_version_content_is_unretrievable(session, qdrant_collection) -> None:  # type: ignore[no-untyped-def]
    """Plan H version-aware retrieval (DOC-5/CLAUDE.md addendum): once a newer
    unapproved version supersedes an older one, `run_embed_upsert`'s
    promote_lineage call demotes the old version and deletes its points, so
    content that ONLY exists in the superseded version must be unretrievable
    -- a user trying to "version-evade" back to secret old content must fail."""
    from tests.modules.retrieval.test_retrieve import seed_workspace

    ctx, ws = await seed_workspace(session, "redteamVer")
    await ingest_text(session, ctx, ws, "policy.txt", "V1-ONLY-SECRET-STRING evacuation plan.")
    await ingest_text(session, ctx, ws, "policy.txt", "V2 evacuation plan, revised.")
    result = await retrieve(session, ctx, ws.id, "V1-ONLY-SECRET-STRING")
    assert not any("V1-ONLY-SECRET-STRING" in c.text for c in result.chunks)
