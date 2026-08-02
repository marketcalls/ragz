"""DOC-10 Task 5: Workspace.embedding_model_id FK + lock-after-first-index.

Every workspace created in this file -- directly via service.create_workspace
or via seed_workspace -- relies on Workspace.embedding_model_id's Python-side
default pointing at LOCAL_EMBEDDING_MODEL_ID. That row is now seeded globally
by tests/conftest.py's `engine` fixture (mirroring migration d1e8f4a2b6c3's
seed INSERT), so no local fixture is needed here.
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from ragz.core.config import Settings
from ragz.core.errors import ConflictError
from ragz.modules.auth.models import User
from ragz.modules.documents.models import Document
from ragz.modules.documents.service import create_from_upload
from ragz.modules.models import service as models_service
from ragz.modules.models.models import LOCAL_EMBEDDING_MODEL_ID, Model
from ragz.modules.tenancy import service
from ragz.modules.tenancy.context import TenantContext
from ragz.modules.tenancy.models import Organization


@pytest.fixture
async def ctx(session: AsyncSession) -> TenantContext:
    # Workspace.org_id FKs to organizations.id, and Document.created_by FKs to
    # users.id -- unlike test_embedding_models.py's identically-shaped ctx
    # fixture (which only ever calls create_model, touching neither table),
    # this file's tests create real Workspace/Document rows (indexed_document_
    # fixture below), so bare uuid4() org_id/user_id would themselves
    # FK-violate. Real Organization + User rows it is.
    org = Organization(name="embedding-lock-org")
    session.add(org)
    await session.flush()
    user = User(
        org_id=org.id, email="embedding-lock@test.com", password_hash="x",  # noqa: S106
        role="superadmin",
    )
    session.add(user)
    await session.flush()
    return TenantContext(
        user_id=user.id, org_id=org.id, role="superadmin",
        workspace_ids=frozenset(), group_ids=frozenset(),
    )


@pytest.fixture
async def embedding_model_fixture(
    session: AsyncSession, ctx: TenantContext, test_settings: Settings
) -> Model:
    return await models_service.create_model(
        session, ctx, litellm_model_name="text-embedding-3-small",
        display_name="OpenAI Small", provider_kind="openai", base_url=None,
        api_key="sk-test", settings=test_settings, modality="embedding", dimension=1536,
    )


@pytest.fixture
async def chat_model_fixture(
    session: AsyncSession, ctx: TenantContext, test_settings: Settings
) -> Model:
    return await models_service.create_model(
        session, ctx, litellm_model_name="gpt-4o-mini",
        display_name="GPT-4o mini", provider_kind="openai", base_url=None,
        api_key="sk-test", settings=test_settings, modality="chat",
    )


@pytest.fixture
async def indexed_document_fixture(
    session: AsyncSession, stack_env: None, ctx: TenantContext
) -> Document:
    """A workspace (in the SAME org as `ctx`, so set_embedding_model's
    get_workspace_checked call below can actually reach it) with one Document
    already status="indexed" -- the state has_indexed_documents' lock check
    is guarding."""
    ws = await service.create_workspace(session, ctx, "embedding-lock-indexed")
    doc = await create_from_upload(
        session, ctx, ws.id, filename="a.pdf", mime="application/pdf", data=b"x"
    )
    doc.status = "indexed"
    await session.commit()
    return doc


async def test_workspace_defaults_to_seeded_local_model(
    session: AsyncSession, ctx: TenantContext
) -> None:
    ws = await service.create_workspace(session, ctx, "test-ws")
    assert ws.embedding_model_id == LOCAL_EMBEDDING_MODEL_ID


async def test_set_embedding_model_switches_when_empty(
    session: AsyncSession, ctx: TenantContext, embedding_model_fixture: Model
) -> None:
    ws = await service.create_workspace(session, ctx, "test-ws")
    updated = await service.set_embedding_model(session, ctx, ws.id, embedding_model_fixture.id)
    assert updated.embedding_model_id == embedding_model_fixture.id


async def test_set_embedding_model_rejects_non_embedding_model(
    session: AsyncSession, ctx: TenantContext, chat_model_fixture: Model
) -> None:
    ws = await service.create_workspace(session, ctx, "test-ws")
    with pytest.raises(ConflictError):
        await service.set_embedding_model(session, ctx, ws.id, chat_model_fixture.id)


async def test_set_embedding_model_409s_once_indexed(
    session: AsyncSession, ctx: TenantContext, embedding_model_fixture: Model,
    indexed_document_fixture: Document,
) -> None:
    ws_id = indexed_document_fixture.workspace_id
    with pytest.raises(ConflictError):
        await service.set_embedding_model(session, ctx, ws_id, embedding_model_fixture.id)
