"""Adversarial leak tests (iron rule 2). Run on every PR.

If any test here fails, treat it as a security incident, not a flake.
"""

import asyncio
from datetime import datetime
from uuid import uuid4

import pytest
from qdrant_client import models
from sqlalchemy.ext.asyncio import AsyncSession

from raghub.core.errors import WorkspaceAccessDenied
from raghub.modules.documents.ingest import run_delete
from raghub.modules.documents.pipeline import Chunk, upsert_hq_points
from raghub.modules.models.models import LOCAL_EMBEDDING_MODEL_ID
from raghub.modules.retrieval.client import COLLECTION, get_qdrant
from raghub.modules.retrieval.embeddings import embed_sparse, get_dense_embedder
from raghub.modules.retrieval.service import (
    MetadataClause,
    _tenant_filter,
    get_chunks_by_refs,
    list_document_chunks,
    retrieve,
)
from tests.isolation.conftest import (
    ingest_text,
    seed_acl_workspace,
    seed_same_org_two_workspaces,
)


def _test_dense_embedder():
    """DOC-10: get_dense_embedder is model-parameterized now; RAGHUB_EMBEDDING_BACKEND=hash
    (set by the stack_env fixture) ignores these args and always returns the
    deterministic hash embedder, matching the seeded LOCAL_EMBEDDING_MODEL_ID row."""
    return get_dense_embedder(
        LOCAL_EMBEDDING_MODEL_ID, provider_kind="tei", litellm_model_name="local-embeddings"
    )


async def test_org_a_never_sees_org_b_chunks(
    session: AsyncSession, two_orgs: dict  # type: ignore[type-arg]
) -> None:
    ctx_a, ws_a, doc_a = two_orgs["a"]
    _, _, doc_b = two_orgs["b"]
    # Query A's workspace with B's exact secret text — the strongest lure possible.
    result = await retrieve(session, ctx_a, ws_a.id,
                            "org bravo secret: the vault code is 9962", top_k=10)
    returned_docs = {c.document_id for c in result.chunks}
    assert doc_b.id not in returned_docs
    assert doc_a.id in returned_docs  # self-contained: not a vacuous empty-result pass
    assert all(d == doc_a.id for d in returned_docs)
    assert all("9962" not in c.text for c in result.chunks)


async def test_same_org_cross_workspace_isolation(
    session: AsyncSession, qdrant_collection: None
) -> None:
    """Same tenant_id, DIFFERENT workspace_id — the real product-leak scenario
    (two teams in one org). Kills the mutant that drops the workspace_id
    must-condition from _tenant_filter while keeping tenant_id: without it,
    ws1's user would see ws2's chunks because both share an org_id."""
    ctx1, ws1, ctx2, ws2 = await seed_same_org_two_workspaces(session)
    doc1 = await ingest_text(session, ctx1, ws1, "ws1.txt",
                             "workspace one secret: the launch code is 5521")
    doc2 = await ingest_text(session, ctx2, ws2, "ws2.txt",
                             "workspace two secret: the launch code is 8834")
    # Query ws1 (ctx1 is a member of ws1 only) with ws2's exact secret as the lure.
    result = await retrieve(session, ctx1, ws1.id,
                            "workspace two secret: the launch code is 8834", top_k=10)
    returned_docs = {c.document_id for c in result.chunks}
    assert doc2.id not in returned_docs
    assert doc1.id in returned_docs
    assert all("8834" not in c.text for c in result.chunks)


def test_tenant_filter_pins_both_tenant_and_workspace_conditions() -> None:
    """Pins the exact shape of the ONE filter builder (iron rule 1): the `must`
    list has to carry BOTH a tenant_id and a workspace_id FieldCondition. Kills
    the mutant that drops the tenant_id condition while keeping workspace_id.
    Phase 2 EXTENSION (not a weakening): acl_group_ids became a required kwarg;
    None means the admin+/maintenance bypass and must produce NO acl clause —
    exactly two conditions, both plain FieldConditions.
    Plan H EXTENSION (not a weakening): current_only/metadata_clauses became
    required kwargs; current_only=False + metadata_clauses=None must still add
    NO clause — exactly two conditions, unchanged from Phase 2."""
    org_id = uuid4()
    workspace_id = uuid4()
    flt = _tenant_filter(
        org_id=org_id, workspace_id=workspace_id,
        acl_group_ids=None, current_only=False, metadata_clauses=None,
    )
    assert flt.must is not None
    assert len(flt.must) == 2  # Phase 2: pins that None adds nothing
    seen = {}
    for cond in flt.must:
        assert isinstance(cond, models.FieldCondition)
        assert isinstance(cond.match, models.MatchValue)
        seen[cond.key] = cond.match.value
    assert seen.get("tenant_id") == str(org_id)
    assert seen.get("workspace_id") == str(workspace_id)


def test_tenant_filter_acl_clause_shape() -> None:
    """Pins the ACL must-clause (RBAC-5): a nested Filter whose `should` is
    exactly [IsEmpty(acl_groups), MatchAny(sorted caller groups)] — point is
    unrestricted OR intersects the caller's groups. must-nesting matters: a
    top-level `should` would let an acl match OVERRIDE the tenant conditions."""
    org_id, workspace_id = uuid4(), uuid4()
    g1, g2 = uuid4(), uuid4()
    flt = _tenant_filter(
        org_id=org_id, workspace_id=workspace_id, acl_group_ids=frozenset({g1, g2}),
        current_only=False, metadata_clauses=None,
    )
    assert flt.must is not None and len(flt.must) == 3
    nested = [c for c in flt.must if isinstance(c, models.Filter)]
    assert len(nested) == 1
    should = nested[0].should
    assert should is not None and len(should) == 2
    empty = [c for c in should if isinstance(c, models.IsEmptyCondition)]
    assert len(empty) == 1 and empty[0].is_empty.key == "acl_groups"
    match = [c for c in should if isinstance(c, models.FieldCondition)]
    assert len(match) == 1 and match[0].key == "acl_groups"
    assert isinstance(match[0].match, models.MatchAny)
    assert match[0].match.any == sorted([str(g1), str(g2)])


def test_tenant_filter_empty_groups_fails_closed() -> None:
    """A caller with NO groups must still get the ACL clause — IsEmpty only,
    never MatchAny(any=[]) (whose semantics we refuse to depend on) and never
    a dropped clause (which would leak every restricted document)."""
    flt = _tenant_filter(
        org_id=uuid4(), workspace_id=uuid4(), acl_group_ids=frozenset(),
        current_only=False, metadata_clauses=None,
    )
    assert flt.must is not None
    nested = [c for c in flt.must if isinstance(c, models.Filter)]
    assert len(nested) == 1
    should = nested[0].should
    assert should is not None and len(should) == 1
    assert isinstance(should[0], models.IsEmptyCondition)
    assert should[0].is_empty.key == "acl_groups"


def test_tenant_filter_current_only_clause_shape() -> None:
    """Pins the is_current clause: a nested must-Filter whose `should` accepts
    is_current==true OR key-absent (legacy pre-H points). Nested-must, like F's
    ACL clause: a top-level should would override the tenant conditions."""
    flt = _tenant_filter(
        org_id=uuid4(), workspace_id=uuid4(),
        acl_group_ids=None, current_only=True, metadata_clauses=None,
    )
    assert flt.must is not None and len(flt.must) == 3
    nested = [c for c in flt.must if isinstance(c, models.Filter)]
    assert len(nested) == 1 and nested[0].should is not None
    kinds = {type(c) for c in nested[0].should}
    assert kinds == {models.FieldCondition, models.IsEmptyCondition}
    field = next(c for c in nested[0].should if isinstance(c, models.FieldCondition))
    assert field.key == "is_current" and isinstance(field.match, models.MatchValue)
    assert field.match.value is True
    empty = next(c for c in nested[0].should if isinstance(c, models.IsEmptyCondition))
    assert empty.is_empty.key == "is_current"


def test_tenant_filter_no_current_clause_when_disabled() -> None:
    flt = _tenant_filter(
        org_id=uuid4(), workspace_id=uuid4(),
        acl_group_ids=None, current_only=False, metadata_clauses=None,
    )
    assert flt.must is not None and len(flt.must) == 2  # tenant + workspace only


def test_tenant_filter_metadata_eq_clause_shape() -> None:
    clause = MetadataClause(key="meta.department", kind="eq", value="HSE")
    flt = _tenant_filter(
        org_id=uuid4(), workspace_id=uuid4(),
        acl_group_ids=None, current_only=False, metadata_clauses=[clause],
    )
    assert flt.must is not None and len(flt.must) == 3
    cond = flt.must[-1]
    assert isinstance(cond, models.FieldCondition)
    assert cond.key == "meta.department" and cond.match.value == "HSE"


def test_tenant_filter_metadata_date_range_clause_shape() -> None:
    clause = MetadataClause(
        key="meta.revision_date", kind="date_range",
        gte="2026-01-01T00:00:00Z", lte="2026-06-30T23:59:59Z",
    )
    flt = _tenant_filter(
        org_id=uuid4(), workspace_id=uuid4(),
        acl_group_ids=None, current_only=False, metadata_clauses=[clause],
    )
    cond = flt.must[-1]
    assert isinstance(cond, models.FieldCondition) and cond.range is not None
    # qdrant_client's DatetimeRange (pinned 1.17-1.18) coerces gte/lte str -> datetime
    # on construction, even when passed a raw ISO string directly -- compare parsed.
    assert cond.range.gte == datetime.fromisoformat("2026-01-01T00:00:00Z")


def test_metadata_clauses_never_displace_tenant_conditions() -> None:
    """Metadata clauses APPEND to must — tenant_id/workspace_id survive."""
    clause = MetadataClause(key="meta.doc_type", kind="eq", value="policy")
    org_id, ws_id = uuid4(), uuid4()
    flt = _tenant_filter(
        org_id=org_id, workspace_id=ws_id,
        acl_group_ids=None, current_only=False, metadata_clauses=[clause],
    )
    keys = [c.key for c in flt.must if isinstance(c, models.FieldCondition)]
    assert "tenant_id" in keys and "workspace_id" in keys


async def test_non_member_workspace_retrieval_denied(
    session: AsyncSession, two_orgs: dict  # type: ignore[type-arg]
) -> None:
    ctx_a, _, _ = two_orgs["a"]
    _, ws_b, _ = two_orgs["b"]
    with pytest.raises(WorkspaceAccessDenied):  # cross-org workspace id
        await retrieve(session, ctx_a, ws_b.id, "anything")


async def test_non_member_same_org_denied(
    session: AsyncSession, two_orgs: dict  # type: ignore[type-arg]
) -> None:
    from dataclasses import replace

    ctx_a, ws_a, _ = two_orgs["a"]
    stranger = replace(ctx_a, workspace_ids=frozenset())  # role "user", no membership
    with pytest.raises(WorkspaceAccessDenied):
        await retrieve(session, stranger, ws_a.id, "anything")


async def test_deleted_document_unretrievable(
    session: AsyncSession, two_orgs: dict  # type: ignore[type-arg]
) -> None:
    ctx_a, ws_a, doc_a = two_orgs["a"]
    before = await retrieve(session, ctx_a, ws_a.id, "vault code 7431")
    assert any(c.document_id == doc_a.id for c in before.chunks)
    await run_delete(doc_a.id, ctx_a.user_id)
    after = await retrieve(session, ctx_a, ws_a.id, "vault code 7431")
    assert all(c.document_id != doc_a.id for c in after.chunks)
    assert all("7431" not in c.text for c in after.chunks)


async def test_canary_unfiltered_query_sees_both_orgs(
    two_orgs: dict,  # type: ignore[type-arg]
) -> None:
    """Prove the data COULD leak without the filter — so the tests above are
    meaningful. This is the only sanctioned unfiltered query in the repo, and it
    lives in tests: production code must never do this (iron rule 1)."""
    ctx_a, *_ = two_orgs["a"]
    lure = (await _test_dense_embedder().embed(["secret: the vault code is"]))[0]
    raw = await get_qdrant().query_points(COLLECTION, query=lure, using="dense",
                                          limit=10, with_payload=True)
    tenants = {str((p.payload or {})["tenant_id"]) for p in raw.points}
    assert len(tenants) == 2  # both orgs visible when the must-filter is absent


async def test_chunk_refs_cross_workspace_never_resolve(
    session: AsyncSession, qdrant_collection: None
) -> None:
    """Citation-backfill leak test (Plan E): a member of ws1 replaying ws2's
    persisted chunk_refs must get NOTHING — same org, different workspace, the
    same product-leak scenario as the retrieval test above. Kills the mutant
    that resolves refs by deterministic point id (bypassing the filter)."""
    ctx1, ws1, ctx2, ws2 = await seed_same_org_two_workspaces(session)
    doc2 = await ingest_text(session, ctx2, ws2, "ws2.txt",
                             "workspace two secret: the launch code is 8834")
    refs = [f"{doc2.id}:1:0"]
    assert await get_chunks_by_refs(ctx1, ws1.id, refs, collection_name=COLLECTION) == []
    # Not a vacuous pass: the same refs DO resolve for the rightful workspace.
    resolved = await get_chunks_by_refs(ctx2, ws2.id, refs, collection_name=COLLECTION)
    assert [c.document_id for c in resolved] == [doc2.id]
    assert "8834" in resolved[0].text


async def test_chunk_refs_cross_org_never_resolve(
    session: AsyncSession, two_orgs: dict  # type: ignore[type-arg]
) -> None:
    ctx_a, ws_a, _ = two_orgs["a"]
    _, _, doc_b = two_orgs["b"]
    # Org A replays org B's chunk_ref against its own workspace: nothing.
    assert (
        await get_chunks_by_refs(ctx_a, ws_a.id, [f"{doc_b.id}:1:0"], collection_name=COLLECTION)
        == []
    )


async def test_document_chunks_cross_workspace_never_resolve(
    session: AsyncSession, qdrant_collection: None
) -> None:
    """Pinned-document leak test (Plan E, review round 1): a member of ws1
    listing ws2's document chunks must get NOTHING — same org, different
    workspace, the same product-leak scenario as the retrieval and
    chunk-refs tests above. Also pins the in-reader membership guard added
    in this round: a ws1-only ctx calling with workspace_id=ws2.id (not the
    filtered document's workspace, but the workspace ARGUMENT itself) must
    raise WorkspaceAccessDenied rather than silently scrolling to []."""
    ctx1, ws1, ctx2, ws2 = await seed_same_org_two_workspaces(session)
    doc2 = await ingest_text(session, ctx2, ws2, "ws2.txt",
                             "workspace two secret: the launch code is 8834")
    # ws1's own member, querying ws1, asking about a document that actually
    # lives in ws2: the tenant filter blocks it -> [].
    assert await list_document_chunks(ctx1, ws1.id, doc2.id, collection_name=COLLECTION) == []
    # Not a vacuous pass: the same document DOES resolve for the rightful
    # workspace/member.
    resolved = await list_document_chunks(ctx2, ws2.id, doc2.id, collection_name=COLLECTION)
    assert resolved
    assert all(c.document_id == doc2.id for c in resolved)
    assert "8834" in resolved[0].text
    # The new in-reader membership guard: ctx1 is not a member of ws2 at all,
    # so calling with workspace_id=ws2.id must raise before any filter runs
    # -- regardless of which document_id is passed.
    with pytest.raises(WorkspaceAccessDenied):
        await list_document_chunks(ctx1, ws2.id, doc2.id, collection_name=COLLECTION)


async def test_hq_point_respects_tenant_and_acl_filters(
    session: AsyncSession, two_orgs: dict  # type: ignore[type-arg]
) -> None:
    """An hq point (Task 4's upsert_hq_points) is a plain Qdrant point written
    through the SAME payload/filter posture as its parent chunk point — it
    must be filtered out by _tenant_filter exactly like a normal chunk point
    would be, for both a cross-org query (tenant clause) and a same-org
    wrong-ACL-group query (ACL clause). Proves Task 4 introduced no second,
    unfiltered read surface (iron rule 1). Each hq point's vector IS the
    hypothetical question's embedding (per spec §4), so querying with the
    EXACT question text is the strongest possible lure: if the tenant/ACL
    filter were broken, this hq point would be the top hit by construction."""
    ctx_a, ws_a, _ = two_orgs["a"]
    ctx_b, ws_b, _ = two_orgs["b"]

    question = "what is the hq secret launch code?"
    q_dense = (await _test_dense_embedder().embed([question]))[0]
    q_sparse = (await asyncio.to_thread(embed_sparse, [question]))[0]
    await upsert_hq_points(
        org_id=ctx_a.org_id, workspace_id=ws_a.id, document_id=uuid4(),
        mime="text/plain", created_at=datetime.now(), acl_group_ids=[],
        version=1, meta=None, is_current=True,
        parent_chunks=[Chunk(text="hq secret: the launch code is 4471", page=1, chunk_index=0)],
        parent_summaries=[None], hq_texts=[[question]],
        hq_dense=[[q_dense]], hq_sparse=[[q_sparse]], collection_name=COLLECTION,
    )
    # Org B, replaying the EXACT question embedded into org A's hq point,
    # must never see it — the tenant clause has to exclude it entirely.
    result_b = await retrieve(session, ctx_b, ws_b.id, question, top_k=10)
    assert not any("4471" in c.text for c in result_b.chunks)
    # Not a vacuous pass: the SAME hq point resolves for its rightful org.
    result_a = await retrieve(session, ctx_a, ws_a.id, question, top_k=10)
    assert any("4471" in c.text for c in result_a.chunks)

    # Same org, one workspace, wrong ACL group — the ACL clause must also
    # exclude an hq point exactly like it excludes its parent chunk.
    ctx_in, ctx_out, _, acl_ws, finance = await seed_acl_workspace(session)
    acl_question = "what is the finance hq secret budget code?"
    acl_dense = (await _test_dense_embedder().embed([acl_question]))[0]
    acl_sparse = (await asyncio.to_thread(embed_sparse, [acl_question]))[0]
    await upsert_hq_points(
        org_id=ctx_in.org_id, workspace_id=acl_ws.id, document_id=uuid4(),
        mime="text/plain", created_at=datetime.now(),
        acl_group_ids=[str(finance.id)],
        version=1, meta=None, is_current=True,
        parent_chunks=[Chunk(text="finance hq secret: the budget code is 8820",
                              page=1, chunk_index=0)],
        parent_summaries=[None], hq_texts=[[acl_question]],
        hq_dense=[[acl_dense]], hq_sparse=[[acl_sparse]], collection_name=COLLECTION,
    )
    result_outsider = await retrieve(session, ctx_out, acl_ws.id, acl_question, top_k=10)
    assert not any("8820" in c.text for c in result_outsider.chunks)
    # Not a vacuous pass: the group member resolves it.
    result_insider = await retrieve(session, ctx_in, acl_ws.id, acl_question, top_k=10)
    assert any("8820" in c.text for c in result_insider.chunks)
