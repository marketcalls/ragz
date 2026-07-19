"""Iron-rule-1 pins for the agent loop (Phase 3 §8).

The structural pin makes the property hold by construction: if agent.py/web.py
cannot name qdrant or the filter builder, tool queries CANNOT escape the
tenant/workspace/ACL fence — they can only go through retrieve()/ChunkReader,
which the rest of this suite already proves safe.
"""

from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

import raghub
from raghub.modules.chat.agent import PlannerAction, execute_tool
from raghub.modules.retrieval.service import RetrievalChunkReader, retrieve
from tests.isolation.conftest import ingest_text, seed_same_org_two_workspaces


def test_agent_and_web_modules_construct_no_qdrant_filters() -> None:
    chat_dir = Path(raghub.__file__).parent / "modules" / "chat"
    for name in ("agent.py", "web.py"):
        src = (chat_dir / name).read_text(encoding="utf-8")
        assert "qdrant_client" not in src, f"{name} must not import qdrant"
        assert "_tenant_filter" not in src, f"{name} must not reach the filter builder"


async def test_agent_search_cannot_cross_workspaces(
    session: AsyncSession, qdrant_collection: None
) -> None:
    """The behavioral pin: execute_tool's "search" action, wired to the REAL
    retrieve() and RetrievalChunkReader, must never surface workspace B's
    secret to a ctx scoped to workspace A only — same org, different
    workspace, the same product-leak scenario as test_tenant_isolation.py."""
    ctx1, ws1, ctx2, ws2 = await seed_same_org_two_workspaces(session)
    secret_b = "workspace two secret: the launch code is 8834"  # noqa: S105 - test lure, not a real secret
    doc_b = await ingest_text(session, ctx2, ws2, "ws2.txt", secret_b)

    out_a = await execute_tool(
        session, ctx1, PlannerAction(action="search", query=secret_b),
        workspace=ws1, retriever=retrieve, chunk_reader=RetrievalChunkReader(),
        web_searcher=None,
    )
    assert out_a.error is None
    assert all(c.document_id != doc_b.id for c in out_a.chunks)
    assert all("8834" not in c.text for c in out_a.chunks)

    # Non-vacuous: the SAME call against workspace B's own ctx DOES return it.
    out_b = await execute_tool(
        session, ctx2, PlannerAction(action="search", query=secret_b),
        workspace=ws2, retriever=retrieve, chunk_reader=RetrievalChunkReader(),
        web_searcher=None,
    )
    assert out_b.error is None
    assert any(c.document_id == doc_b.id for c in out_b.chunks)
    assert any("8834" in c.text for c in out_b.chunks)
