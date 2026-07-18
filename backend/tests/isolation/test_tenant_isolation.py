"""Adversarial leak tests (iron rule 2). Run on every PR.

If any test here fails, treat it as a security incident, not a flake.
"""

from uuid import uuid4

import pytest
from qdrant_client import models
from sqlalchemy.ext.asyncio import AsyncSession

from raghub.core.errors import WorkspaceAccessDenied
from raghub.modules.documents.ingest import run_delete
from raghub.modules.retrieval.client import COLLECTION, get_qdrant
from raghub.modules.retrieval.embeddings import get_dense_embedder
from raghub.modules.retrieval.service import _tenant_filter, get_chunks_by_refs, retrieve
from tests.isolation.conftest import ingest_text, seed_same_org_two_workspaces


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
    the mutant that drops the tenant_id condition while keeping workspace_id
    (a mutant the black-box retrieval tests above can't distinguish from the
    correct filter when org A and org B never share a workspace_id). Reaching
    into the private `_tenant_filter` is intentional and sanctioned only in
    this isolation suite."""
    org_id = uuid4()
    workspace_id = uuid4()
    flt = _tenant_filter(org_id=org_id, workspace_id=workspace_id)
    assert flt.must is not None
    seen = {}
    for cond in flt.must:
        assert isinstance(cond, models.FieldCondition)
        assert isinstance(cond.match, models.MatchValue)
        seen[cond.key] = cond.match.value
    assert seen.get("tenant_id") == str(org_id)
    assert seen.get("workspace_id") == str(workspace_id)


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
    lure = (await get_dense_embedder().embed(["secret: the vault code is"]))[0]
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
    assert await get_chunks_by_refs(ctx1, ws1.id, refs) == []
    # Not a vacuous pass: the same refs DO resolve for the rightful workspace.
    resolved = await get_chunks_by_refs(ctx2, ws2.id, refs)
    assert [c.document_id for c in resolved] == [doc2.id]
    assert "8834" in resolved[0].text


async def test_chunk_refs_cross_org_never_resolve(
    session: AsyncSession, two_orgs: dict  # type: ignore[type-arg]
) -> None:
    ctx_a, ws_a, _ = two_orgs["a"]
    _, _, doc_b = two_orgs["b"]
    # Org A replays org B's chunk_ref against its own workspace: nothing.
    assert await get_chunks_by_refs(ctx_a, ws_a.id, [f"{doc_b.id}:1:0"]) == []
