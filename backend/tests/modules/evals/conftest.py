"""Local fixtures for the eval-harness (golden-query) service tests.

Mirrors tests/modules/retrieval/test_retrieve.py::seed_workspace (the
established org+workspace+user seeding pattern) rather than inventing a new
one. `ctx`/`ws` use role="admin" so the fixture ctx can create a document in
a SECOND workspace in the SAME org for `other_ws_document` without also
needing a WorkspaceMember row there (get_workspace_checked only enforces the
membership check for role="user" -- see tenancy/service.py).
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from raghub.modules.documents.models import Document
from raghub.modules.documents.service import create_from_upload
from raghub.modules.tenancy.context import TenantContext
from raghub.modules.tenancy.models import Workspace
from tests.modules.retrieval.test_retrieve import seed_workspace


@pytest.fixture
async def ctx_ws(
    session: AsyncSession, stack_env: None
) -> tuple[TenantContext, Workspace]:
    return await seed_workspace(session, "evals", role="admin")


@pytest.fixture
async def ctx(ctx_ws: tuple[TenantContext, Workspace]) -> TenantContext:
    return ctx_ws[0]


@pytest.fixture
async def ws(ctx_ws: tuple[TenantContext, Workspace]) -> Workspace:
    return ctx_ws[1]


@pytest.fixture
async def other_ws_document(
    session: AsyncSession, ctx: TenantContext, ws: Workspace
) -> Document:
    """A document that lives in a DIFFERENT workspace in the SAME org as `ws`
    -- the precise scenario create_golden_query's workspace-scoped id check
    must reject (never post-filtered, never silently ignored)."""
    other_ws = Workspace(org_id=ctx.org_id, name="other-ws")
    session.add(other_ws)
    await session.flush()
    await session.commit()
    return await create_from_upload(
        session, ctx, other_ws.id, filename="other.pdf", mime="application/pdf", data=b"y"
    )
