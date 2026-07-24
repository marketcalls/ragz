from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from raghub.core.config import get_settings
from raghub.core.errors import ConflictError
from raghub.modules.models import service
from raghub.modules.models.models import LOCAL_EMBEDDING_MODEL_ID
from raghub.modules.tenancy.context import TenantContext


@pytest.fixture
def ctx() -> TenantContext:
    return TenantContext(
        user_id=uuid4(), org_id=uuid4(), role="superadmin",
        workspace_ids=frozenset(), group_ids=frozenset(),
    )


async def test_create_embedding_model_computes_collection_name(
    session: AsyncSession, ctx: TenantContext
) -> None:
    model = await service.create_model(
        session, ctx, litellm_model_name="text-embedding-3-small",
        display_name="OpenAI Small", provider_kind="openai", base_url=None,
        api_key="sk-test", settings=get_settings(), modality="embedding", dimension=1536,
    )
    assert model.collection_name == f"chunks_{model.id.hex}"
    assert model.dimension == 1536


async def test_delete_tei_model_rejected(
    session: AsyncSession, ctx: TenantContext
) -> None:
    with pytest.raises(ConflictError):
        await service.delete_model(session, ctx, LOCAL_EMBEDDING_MODEL_ID, settings=get_settings())
