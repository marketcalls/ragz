"""Golden-query admin CRUD (Phase 3 §6). Org-scoped, workspace-owned."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from raghub.core.errors import NotFoundError
from raghub.modules.documents.service import create_from_upload
from raghub.modules.evals import service
from raghub.modules.tenancy.context import TenantContext
from raghub.modules.tenancy.models import Workspace


async def test_create_and_list_golden_query(
    session: AsyncSession, ctx: TenantContext, ws: Workspace
) -> None:
    doc = await create_from_upload(
        session, ctx, ws.id, filename="a.pdf", mime="application/pdf", data=b"x"
    )
    gq = await service.create_golden_query(
        session, ctx, ws.id, question="Where is the muster point?",
        expected_document_ids=[doc.id],
    )
    assert gq.question == "Where is the muster point?"
    listed = await service.list_golden_queries(session, ctx, ws.id)
    assert [g.id for g in listed] == [gq.id]


async def test_create_rejects_document_outside_workspace(
    session: AsyncSession, ctx: TenantContext, ws: Workspace, other_ws_document,
) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(NotFoundError):
        await service.create_golden_query(
            session, ctx, ws.id, question="q", expected_document_ids=[other_ws_document.id]
        )


async def test_create_allows_zero_expected_documents(
    session: AsyncSession, ctx: TenantContext, ws: Workspace
) -> None:
    gq = await service.create_golden_query(
        session, ctx, ws.id, question="Off-corpus question", expected_document_ids=[]
    )
    assert gq.expected_document_ids == []


async def test_delete_golden_query(
    session: AsyncSession, ctx: TenantContext, ws: Workspace
) -> None:
    gq = await service.create_golden_query(
        session, ctx, ws.id, question="q", expected_document_ids=[]
    )
    await service.delete_golden_query(session, ctx, gq.id)
    assert await service.list_golden_queries(session, ctx, ws.id) == []
