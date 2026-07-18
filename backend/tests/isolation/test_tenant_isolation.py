"""Adversarial leak tests (iron rule 2). Run on every PR.

If any test here fails, treat it as a security incident, not a flake.
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from raghub.core.errors import WorkspaceAccessDenied
from raghub.modules.documents.ingest import run_delete
from raghub.modules.retrieval.client import COLLECTION, get_qdrant
from raghub.modules.retrieval.embeddings import get_dense_embedder
from raghub.modules.retrieval.service import retrieve


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
    assert all(d == doc_a.id for d in returned_docs)
    assert all("9962" not in c.text for c in result.chunks)


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
