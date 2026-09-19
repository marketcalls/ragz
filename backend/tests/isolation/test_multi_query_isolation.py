from sqlalchemy.ext.asyncio import AsyncSession

from ragz.modules.retrieval.query_expansion import ExpandedQueries
from ragz.modules.retrieval.service import retrieve
from tests.modules.retrieval.test_retrieve import seed_workspace, upsert_texts


class _CrossTenantMatchingExpander:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def expand(self, query: str, *, model: str) -> ExpandedQueries:
        self.calls.append((query, model))
        return ExpandedQueries((query, "rival organization secret phrase"))


async def test_multi_query_variants_cannot_cross_tenant_filter(
    session: AsyncSession,
    qdrant_collection: None,
    utility_model: object,
) -> None:
    caller_ctx, caller_ws = await seed_workspace(
        session, "mq-caller", multi_query_enabled=True
    )
    rival_ctx, rival_ws = await seed_workspace(session, "mq-rival")
    rival_document_id = await upsert_texts(
        rival_ctx,
        rival_ws,
        ["rival organization secret phrase"],
    )
    caller_document_id = await upsert_texts(caller_ctx, caller_ws, ["caller approved evidence"])
    expander = _CrossTenantMatchingExpander()

    result = await retrieve(
        session,
        caller_ctx,
        caller_ws.id,
        "caller approved evidence",
        query_expander=expander,
    )

    assert expander.calls == [("caller approved evidence", "utility-model")]
    assert caller_document_id in {str(chunk.document_id) for chunk in result.chunks}
    assert rival_document_id not in {str(chunk.document_id) for chunk in result.chunks}
