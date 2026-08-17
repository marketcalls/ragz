"""ACL tightening must fail CLOSED when the vector store is unavailable.

Architecture review 2026-08-17, P0 "ACL changes can be inconsistent across
Postgres and Qdrant": `set_document_acl` commits the restriction to Postgres and
only THEN calls Qdrant (documents/service.py). If Qdrant is down in between, the
row is restricted while the vector payload still carries the old, broader ACL --
so retrieval keeps serving the document to users who, per Postgres, may no
longer open it. The route returning 502 is not a safety mechanism: nothing stops
retrieval from using the stale payload until a human retries.

That directly contradicts the foundational promise that an answer can never cite
a document the asking user cannot open.

These tests assert the SAFE behaviour, so they fail while the exposure exists.
They are xfail(strict=True): the suite stays green today, and the moment the
revisioned-projection fix lands they XPASS, which strict mode reports as a
failure telling you to delete the marker. That is deliberate -- it makes the fix
self-announcing rather than leaving a stale "known issue" comment behind.

The fix these wait on (review §P0, Phase 1): document index_revision /
desired_index_revision / index_state written in the SAME commit as the ACL
change, an idempotent projection worker, the active revision included in the
Qdrant predicate, and a reconciler. Until then a document whose security
revision is unprojected must not be retrievable.
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from ragz.modules.documents.service import set_document_acl
from ragz.modules.retrieval import service as retrieval_service
from ragz.modules.retrieval.service import retrieve
from tests.isolation.conftest import ingest_text, seed_acl_workspace

# The lure the retrieval query searches for -- document content, not a
# credential (S105 matches on the name).
SECRET = "finance secret: the acquisition price is 4400"  # noqa: S105


class _QdrantDown(Exception):
    """Stands in for any Qdrant transport failure during the projection."""


async def _tighten_with_qdrant_down(
    monkeypatch: pytest.MonkeyPatch, session: AsyncSession, ctx_admin, doc_id, group_id
) -> None:  # type: ignore[no-untyped-def]
    """Restrict the document while the vector store is unreachable.

    Postgres commits; the Qdrant projection raises. This is exactly the window
    the review describes, and the caller (the route) can only turn it into a 502.
    """

    async def _boom(*_args: object, **_kwargs: object) -> None:
        raise _QdrantDown("qdrant unavailable")

    monkeypatch.setattr(retrieval_service, "update_document_acl", _boom)
    with pytest.raises(_QdrantDown):
        await set_document_acl(session, ctx_admin, doc_id, [group_id])
    monkeypatch.undo()


@pytest.mark.xfail(
    strict=True,
    reason=(
        "P0: set_document_acl commits to Postgres before projecting to Qdrant, so a "
        "Qdrant outage leaves the OLD unrestricted payload searchable. Remove this "
        "marker when revisioned projections land."
    ),
)
async def test_tightening_an_acl_during_a_qdrant_outage_does_not_over_grant(
    session: AsyncSession, qdrant_collection: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx_in, ctx_out, ctx_admin, ws, finance = await seed_acl_workspace(session)
    # Starts UNRESTRICTED and indexed: the outsider legitimately sees it now.
    doc = await ingest_text(session, ctx_admin, ws, "secret.txt", SECRET)
    before = await retrieve(session, ctx_out, ws.id, SECRET, top_k=10)
    assert doc.id in {c.document_id for c in before.chunks}, "baseline: doc is open"

    await _tighten_with_qdrant_down(monkeypatch, session, ctx_admin, doc.id, finance.id)

    # Postgres now says "finance only", and the outsider is not in finance.
    await session.refresh(doc)
    assert doc.acl_group_ids == [finance.id], "the restriction did commit to Postgres"

    # The security claim: no retrieval may serve a document whose restriction is
    # committed but not yet projected. Today the stale payload still matches.
    after = await retrieve(session, ctx_out, ws.id, SECRET, top_k=10)
    assert doc.id not in {c.document_id for c in after.chunks}
    assert all("4400" not in c.text for c in after.chunks)


@pytest.mark.xfail(
    strict=True,
    reason=(
        "P0: same unprojected-revision window as the tighten case. Remove this marker "
        "when revisioned projections land."
    ),
)
async def test_the_permitted_group_also_loses_access_while_a_revision_is_unprojected(
    session: AsyncSession, qdrant_collection: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fail-closed means closed for EVERYONE, not just the newly-excluded user.

    While a security revision is unprojected, the vector payload does not
    reflect the committed intent, so no caller's access can be evaluated
    correctly -- including a caller the new ACL happens to permit. Serving them
    would mean trusting the same stale payload that over-grants the outsider,
    and a brief under-grant is the correct trade for never over-granting.
    """
    ctx_in, _ctx_out, ctx_admin, ws, finance = await seed_acl_workspace(session)
    doc = await ingest_text(session, ctx_admin, ws, "secret.txt", SECRET)

    await _tighten_with_qdrant_down(monkeypatch, session, ctx_admin, doc.id, finance.id)

    result = await retrieve(session, ctx_in, ws.id, SECRET, top_k=10)
    assert doc.id not in {c.document_id for c in result.chunks}
