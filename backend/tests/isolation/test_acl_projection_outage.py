"""ACL tightening must fail CLOSED when the vector store is unavailable.

Architecture review 2026-08-17, P0 "ACL changes can be inconsistent across
Postgres and Qdrant": `set_document_acl` USED TO commit the restriction to
Postgres and only then call Qdrant. If Qdrant was down in between, the row was
restricted while the vector payload still carried the old, broader ACL -- so
retrieval kept serving the document to users who, per Postgres, could no longer
open it. The route's 502 was not a safety mechanism: nothing stopped retrieval
from using the stale payload until a human retried.

That directly contradicts the foundational promise that an answer can never cite
a document the asking user cannot open.

CLOSED by the fail-closed projection (migration b1c4e7a20d31): documents carry
security_revision / projected_security_revision / index_state, the ACL change and
the revision bump land in ONE commit, and retrieval excludes any document whose
committed revision has not been projected -- as a must_not INSIDE the vector
query, never a Python post-filter (iron rule 2).

These began life as xfail(strict=True) so the suite stayed green while the hole
was open and the fix would announce itself by XPASSing. It did exactly that;
the markers came off in the same commit that closed the hole. They now guard
against regression.
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
    # committed but not yet projected -- the stale payload would still match it.
    after = await retrieve(session, ctx_out, ws.id, SECRET, top_k=10)
    assert doc.id not in {c.document_id for c in after.chunks}
    assert all("4400" not in c.text for c in after.chunks)


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


async def test_the_reconciler_reopens_access_once_qdrant_is_reachable_again(
    session: AsyncSession, qdrant_collection: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fail-closed must not mean fail-forever.

    Hiding the document on a Qdrant failure is only defensible if it comes back
    by itself. Without the reconciler one blip would leave it invisible until an
    operator noticed and re-saved the ACL by hand -- trading a security bug for
    a silent availability bug.
    """
    from ragz.modules.documents.ingest import reconcile_security_projections
    from ragz.modules.documents.models import Document

    ctx_in, ctx_out, ctx_admin, ws, finance = await seed_acl_workspace(session)
    doc = await ingest_text(session, ctx_admin, ws, "secret.txt", SECRET)

    await _tighten_with_qdrant_down(monkeypatch, session, ctx_admin, doc.id, finance.id)

    await session.refresh(doc)
    assert doc.index_state == "failed", "the failed projection is recorded, not lost"

    # Qdrant is healthy again; the sweep re-drives the committed ACL.
    recovered = await reconcile_security_projections()
    assert recovered == 1

    row = await session.get(Document, doc.id)
    assert row is not None
    await session.refresh(row)
    assert row.index_state == "active"
    assert row.projected_security_revision == row.security_revision

    # And the ACL that finally landed is the CORRECT one, not the stale payload:
    # the permitted group sees it, the outsider still does not.
    permitted = await retrieve(session, ctx_in, ws.id, SECRET, top_k=10)
    assert doc.id in {c.document_id for c in permitted.chunks}
    denied = await retrieve(session, ctx_out, ws.id, SECRET, top_k=10)
    assert doc.id not in {c.document_id for c in denied.chunks}
