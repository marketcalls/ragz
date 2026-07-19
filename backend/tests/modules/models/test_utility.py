"""Utility-model resolver and 'exactly one' enforcement (Phase 3 Plan J,
design D5/§4). Follows this module's existing test convention: a local
`settings` fixture + `super_ctx(user)` helper (see test_service.py /
test_mock_response.py) rather than shared `ctx`/`test_settings` fixtures,
which don't exist in this package."""

from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from raghub.core.config import Settings
from raghub.modules.auth.models import User
from raghub.modules.models import service
from raghub.modules.models.models import Model
from raghub.modules.models.utility import get_utility_model
from raghub.modules.secrets.crypto import ensure_kek
from raghub.modules.tenancy.context import TenantContext


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    kek = tmp_path / "kek"
    ensure_kek(str(kek))
    return Settings(_env_file=None, kek_file=str(kek))


def super_ctx(user: User) -> TenantContext:
    return TenantContext(
        user_id=user.id, org_id=user.org_id, role="superadmin", workspace_ids=frozenset()
    )


async def _make(
    session: AsyncSession, ctx: TenantContext, settings: Settings, name: str
) -> Model:
    return await service.create_model(
        session, ctx, litellm_model_name=name, display_name=name,
        provider_kind="ollama", base_url="http://ollama:11434",
        api_key=None, settings=settings,
    )


async def test_no_utility_model_resolves_none(
    session: AsyncSession, seeded_user: User, settings: Settings
) -> None:
    ctx = super_ctx(seeded_user)
    await _make(session, ctx, settings, "m1")
    assert await get_utility_model(session) is None


async def test_setting_one_clears_the_previous(
    session: AsyncSession, seeded_user: User, settings: Settings
) -> None:
    ctx = super_ctx(seeded_user)
    a = await _make(session, ctx, settings, "a")
    b = await _make(session, ctx, settings, "b")
    await service.update_model(
        session, ctx, a.id, display_name=None, base_url=None, enabled=None,
        api_key=None, settings=settings, is_utility=True,
    )
    resolved = await get_utility_model(session)
    assert resolved is not None and resolved.id == a.id

    await service.update_model(
        session, ctx, b.id, display_name=None, base_url=None, enabled=None,
        api_key=None, settings=settings, is_utility=True,
    )
    resolved = await get_utility_model(session)
    assert resolved is not None and resolved.id == b.id
    refreshed_a = (await session.execute(select(Model).where(Model.id == a.id))).scalar_one()
    assert refreshed_a.is_utility is False  # exactly one, enforced


async def test_disabled_utility_model_resolves_none(
    session: AsyncSession, seeded_user: User, settings: Settings
) -> None:
    ctx = super_ctx(seeded_user)
    a = await _make(session, ctx, settings, "a")
    await service.update_model(
        session, ctx, a.id, display_name=None, base_url=None, enabled=None,
        api_key=None, settings=settings, is_utility=True,
    )
    await service.update_model(
        session, ctx, a.id, display_name=None, base_url=None, enabled=False,
        api_key=None, settings=settings,
    )
    assert await get_utility_model(session) is None


async def test_unsetting_only_clears_itself(
    session: AsyncSession, seeded_user: User, settings: Settings
) -> None:
    ctx = super_ctx(seeded_user)
    a = await _make(session, ctx, settings, "a")
    b = await _make(session, ctx, settings, "b")
    for m in (a, b):
        await service.update_model(
            session, ctx, m.id, display_name=None, base_url=None, enabled=None,
            api_key=None, settings=settings, is_utility=True,
        )
    # b is now the sole utility model (a was cleared by b's own set-True).
    await service.update_model(
        session, ctx, b.id, display_name=None, base_url=None, enabled=None,
        api_key=None, settings=settings, is_utility=False,
    )
    assert await get_utility_model(session) is None
