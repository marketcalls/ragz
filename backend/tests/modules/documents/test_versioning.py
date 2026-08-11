"""Effective-current promotion semantics (DOC-5 + approved preference).

upload v1 -> index -> v1 current
upload v2 -> index -> v2 current, v1 demoted, v1 points deleted
approve v1 only    -> v1 current again (approved beats newer-unapproved) via reindex
approve v2 as well -> v2 current (highest approved wins)
"""

from sqlalchemy.ext.asyncio import AsyncSession

from ragz.core.app_settings import set_app_setting
from ragz.modules.documents.ingest import run_chunk, run_delete, run_embed_upsert, run_parse
from ragz.modules.documents.models import Document
from ragz.modules.documents.service import create_from_upload, promote_lineage, set_approved
from ragz.modules.retrieval.service import retrieve
from ragz.modules.tenancy.context import TenantContext
from ragz.modules.tenancy.models import Workspace
from tests.modules.retrieval.test_retrieve import seed_workspace


async def _index(
    session: AsyncSession, ctx: TenantContext, ws: Workspace, filename: str, text: str
) -> Document:
    """Real pipeline: upload -> parse -> chunk -> embed+upsert (which now ends
    by calling promote_lineage per DOC-5). No manual update_document_current
    stand-in needed anymore -- that was Task 5's placeholder.

    Plain .txt fixture content -- anydoc (the install-wide default) does not
    parse .txt at all; this suite is about version-promotion semantics, not
    parser choice, so pin docling explicitly.
    """
    await set_app_setting(session, "document_parser", "docling")
    doc = await create_from_upload(
        session, ctx, ws.id, filename=filename, mime="text/plain", data=text.encode()
    )
    await run_parse(doc.id)
    await run_chunk(doc.id)
    await run_embed_upsert(doc.id)
    await session.refresh(doc)
    return doc


async def test_first_index_promotes_v1(
    session: AsyncSession, qdrant_collection: None
) -> None:
    ctx, ws = await seed_workspace(session, "ver1")
    v1 = await _index(session, ctx, ws, "policy.txt", "the muster point is DOCK 4")
    assert v1.is_current is True
    assert v1.vectors_present is True


async def test_new_indexed_version_supersedes(
    session: AsyncSession, qdrant_collection: None
) -> None:
    ctx, ws = await seed_workspace(session, "ver2")
    v1 = await _index(session, ctx, ws, "policy.txt", "the muster point is DOCK 4")
    v2 = await _index(session, ctx, ws, "policy.txt", "the muster point is GATE 9")
    await session.refresh(v1)
    await session.refresh(v2)
    assert v2.is_current is True
    assert v1.is_current is False
    assert v1.vectors_present is False
    assert v2.vectors_present is True


async def test_approved_older_version_wins(
    session: AsyncSession, qdrant_collection: None
) -> None:
    ctx, ws = await seed_workspace(session, "ver3")
    v1 = await _index(session, ctx, ws, "policy.txt", "the muster point is DOCK 4")
    v1, needs_reindex = await set_approved(session, ctx, v1.id, True)
    assert needs_reindex is None  # v1 still has its own points -- flip, no reindex needed
    v2 = await _index(session, ctx, ws, "policy.txt", "the muster point is GATE 9")
    await session.refresh(v1)
    await session.refresh(v2)
    assert v1.is_current is True
    assert v1.vectors_present is True
    assert v2.is_current is False
    assert v2.vectors_present is False  # points invisible: deleted on the same promotion


async def test_approving_pointless_version_enqueues_reindex(
    session: AsyncSession, qdrant_collection: None,
) -> None:
    """Plan K Task 11: set_approved no longer enqueues the reindex itself --
    it returns the needs-reindex document id and its ENTRYPOINT caller (the
    approve route in production; this test stands in for that entrypoint)
    is responsible for the actual enqueue_reindex call."""
    ctx, ws = await seed_workspace(session, "ver4")
    v1 = await _index(session, ctx, ws, "policy.txt", "the muster point is DOCK 4")
    v2 = await _index(session, ctx, ws, "policy.txt", "the muster point is GATE 9")
    await session.refresh(v1)
    assert v1.vectors_present is False  # demoted+deleted when v2 was promoted

    v1, needs_reindex = await set_approved(session, ctx, v1.id, True)
    assert needs_reindex == v1.id  # no points to promote onto -> reindex needed instead
    await session.refresh(v1)
    await session.refresh(v2)
    assert v1.is_current is False  # unchanged: flags don't flip until reindex completes
    assert v2.is_current is True

    # Simulate the reindex task completing: run_embed_upsert re-reads chunks.json
    # (deterministic point ids) and its tail re-runs promote_lineage.
    await run_embed_upsert(v1.id)
    await session.refresh(v1)
    await session.refresh(v2)
    assert v1.is_current is True
    assert v2.is_current is False


async def test_unapprove_falls_back_to_newest_via_reindex(
    session: AsyncSession, qdrant_collection: None
) -> None:
    """Approving v1 promotes it (v2 demoted, its points deleted). Unapproving
    v1 again makes v2 (the highest indexed row, now that nothing is approved)
    the new effective version -- but v2's points were deleted earlier, so
    promote_lineage must enqueue a reindex rather than flip flags onto a
    pointless winner. After the reindex completes (run_embed_upsert), exactly
    one row is is_current (v2) and only v2's content is retrievable."""
    ctx, ws = await seed_workspace(session, "ver6")
    v1 = await _index(session, ctx, ws, "policy.txt", "the muster point is DOCK 4")
    v2 = await _index(session, ctx, ws, "policy.txt", "the muster point is GATE 9")
    await session.refresh(v1)  # un-expire before reuse (matches the sibling tests' pattern)
    v1, needs_reindex = await set_approved(session, ctx, v1.id, True)
    assert needs_reindex == v1.id  # v1's points were deleted at v2's promotion
    await run_embed_upsert(v1.id)  # simulate the reindex task completing
    await session.refresh(v1)
    await session.refresh(v2)
    assert v1.is_current is True
    assert v2.is_current is False
    assert v2.vectors_present is False  # deleted when v1 (approved) was promoted

    v1, needs_reindex = await set_approved(session, ctx, v1.id, False)
    assert needs_reindex == v2.id  # new effective (v2) has no points yet -- reindex needed
    await session.refresh(v1)
    await session.refresh(v2)
    assert v1.is_current is True  # unchanged: new effective (v2) has no points yet
    assert v2.is_current is False

    await run_embed_upsert(v2.id)  # simulate the reindex task completing
    await session.refresh(v1)
    await session.refresh(v2)
    assert v1.is_current is False
    assert v2.is_current is True
    assert v1.vectors_present is False
    assert v2.vectors_present is True

    result = await retrieve(session, ctx, ws.id, "muster point", top_k=10)
    returned_docs = {c.document_id for c in result.chunks}
    assert returned_docs == {v2.id}  # exactly one lineage member visible, and it's v2


async def test_approving_both_versions_highest_approved_wins(
    session: AsyncSession, qdrant_collection: None
) -> None:
    """Docstring-promised scenario: v1 approved (wins over unapproved v2);
    then v2 is ALSO approved -> among approved rows the highest version wins,
    so v2 becomes current again. v2's points were deleted by v1's earlier
    promotion, so this too routes through a reindex before flags flip."""
    ctx, ws = await seed_workspace(session, "ver8")
    v1 = await _index(session, ctx, ws, "policy.txt", "the muster point is DOCK 4")
    v2 = await _index(session, ctx, ws, "policy.txt", "the muster point is GATE 9")
    await session.refresh(v1)  # un-expire before reuse (matches the sibling tests' pattern)
    v1, needs_reindex = await set_approved(session, ctx, v1.id, True)
    assert needs_reindex == v1.id  # v1's points were deleted at v2's promotion
    await run_embed_upsert(v1.id)  # simulate the reindex task completing
    await session.refresh(v1)
    await session.refresh(v2)
    assert v1.is_current is True
    assert v2.is_current is False

    v2, needs_reindex = await set_approved(session, ctx, v2.id, True)
    assert needs_reindex == v2.id  # v2 has no points yet -- reindex needed
    await session.refresh(v1)
    await session.refresh(v2)
    assert v1.is_current is True  # unchanged: v2 has no points yet, reindex pending
    assert v2.is_current is False

    await run_embed_upsert(v2.id)  # simulate the reindex task completing
    await session.refresh(v1)
    await session.refresh(v2)
    assert v2.is_current is True  # both approved -> highest version wins
    assert v1.is_current is False


async def test_delete_current_version_promotes_survivor(
    session: AsyncSession, qdrant_collection: None,
) -> None:
    """Plan K Task 11: run_delete's tail returns the needs-reindex document id
    (from promote_lineage) instead of enqueueing itself -- worker/tasks.py's
    delete_task performs the actual enqueue_reindex call in production."""
    ctx, ws = await seed_workspace(session, "ver5")
    v1 = await _index(session, ctx, ws, "policy.txt", "the muster point is DOCK 4")
    v2 = await _index(session, ctx, ws, "policy.txt", "the muster point is GATE 9")
    await session.refresh(v1)
    assert v1.vectors_present is False  # demoted+deleted when v2 was promoted

    needs_reindex = await run_delete(v2.id, ctx.user_id)
    assert needs_reindex == v1.id  # only surviving version has no points -> reindex needed
    await session.refresh(v1)
    assert v1.is_current is False  # unchanged until the reindex completes

    await run_embed_upsert(v1.id)  # simulate the reindex task
    await session.refresh(v1)
    assert v1.is_current is True
    assert v1.vectors_present is True


async def test_promote_lineage_returns_needs_reindex_id_without_enqueuing(
    session: AsyncSession, qdrant_collection: None,
) -> None:
    """Plan K Task 11 (carried K-C7 fix): promote_lineage returns the
    needs-reindex document id instead of enqueueing itself. Same
    v1-approved-after-v2-deleted-its-points scenario as
    test_approving_pointless_version_enqueues_reindex, but promote_lineage is
    called directly (bypassing set_approved) so the return contract is pinned
    in isolation from any caller-side enqueue behavior."""
    ctx, ws = await seed_workspace(session, "ver-promote-return")
    v1 = await _index(session, ctx, ws, "policy.txt", "the muster point is DOCK 4")
    await _index(session, ctx, ws, "policy.txt", "the muster point is GATE 9")
    await session.refresh(v1)
    assert v1.vectors_present is False  # demoted+deleted when v2 was promoted

    v1.approved = True
    await session.commit()

    result = await promote_lineage(session, ctx.org_id, v1.lineage_id)
    assert result == v1.id


def test_import_linter_documents_service_worker_exception_removed() -> None:
    """Plan K Task 11's load-bearing assertion: the ignore_imports entry that
    excused documents/service.py's promote_lineage from importing
    worker.tasks is gone now that the enqueue moved to the entrypoints. This
    intentionally does NOT assert the ignore_imports list is empty overall --
    Plan J's Task 12 added an unrelated, still-live exception for
    tenancy.service -> worker.tasks (the eval-run settings-change trigger),
    which is out of this task's scope and must remain."""
    import tomllib

    with open("pyproject.toml", "rb") as f:
        config = tomllib.load(f)
    contracts = config["tool"]["importlinter"]["contracts"]
    ignored = {
        entry for contract in contracts for entry in contract.get("ignore_imports", [])
    }
    assert "ragz.modules.documents.service -> ragz.worker.tasks" not in ignored
