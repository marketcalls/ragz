"""Utility-model resolver and 'exactly one' enforcement (Phase 3 Plan J,
design D5/§4). Follows this module's existing test convention: a local
`settings` fixture + `super_ctx(user)` helper (see test_service.py /
test_mock_response.py) rather than shared `ctx`/`test_settings` fixtures,
which don't exist in this package.

Plan K's actual plan document (Tasks 6, 7, 9 -- ingestion enrichment,
backfill, rolling-summary fold-in) calls `models_service.resolve_utility_model
(session)`, not `get_utility_model`. `resolve_utility_model` is a one-line
re-export of this module's `get_utility_model` added in
`modules/models/service.py`, so the single real query implementation stays
here per the single-seam convention -- see `test_alias_resolves_same_model`
below for the pin proving the two names resolve identically."""

import re
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import raghub.modules as modules_pkg
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


def test_is_utility_has_exactly_one_query_site() -> None:
    """Plan K Task 1 go/no-go pin (K-C16): guards against a second resolver
    ever being introduced that queries `Model.is_utility` directly (e.g. a
    parallel query defined fresh in models/service.py rather than re-exported
    from here), which would violate the single-seam convention this module's
    own docstring claims. Matches `select(Model).where(...)` calls whose
    *entire* `.where(...)` argument list contains `is_utility` anywhere --
    not just as the first clause, so a query like `.where(Model.enabled ==
    true(), Model.is_utility == true())` is still caught. Writes such as
    service.py's exclusivity-enforcing `update(Model)...values(is_utility=
    False)` and the plain attribute assignment `model.is_utility = True` are
    not query sites and must NOT trip this pin."""
    pattern = re.compile(
        r"select\(Model\)\.where\((?:[^()]|\([^()]*\))*is_utility(?:[^()]|\([^()]*\))*\)",
        re.DOTALL,
    )
    modules_root = Path(modules_pkg.__file__).resolve().parent
    hits: list[str] = []
    for path in modules_root.rglob("*.py"):
        text = path.read_text()
        if pattern.search(text):
            hits.append(str(path.relative_to(modules_root)))
    assert hits == [str(Path("models") / "utility.py")], (
        f"expected the ONE is_utility query site to be models/utility.py, found: {hits}"
    )


async def test_alias_resolves_same_model(
    session: AsyncSession, seeded_user: User, settings: Settings
) -> None:
    """Plan K Tasks 6/7/9 call `models_service.resolve_utility_model(session)`
    (the plan document's actual call shape), not `get_utility_model`.
    `resolve_utility_model` is a one-line re-export in modules/models/
    service.py, not a second implementation -- prove it resolves to the
    exact same model `get_utility_model` would."""
    ctx = super_ctx(seeded_user)
    assert await service.resolve_utility_model(session) is None
    m = await _make(session, ctx, settings, "utility-candidate")
    await service.update_model(
        session, ctx, m.id, display_name=None, base_url=None, enabled=None,
        api_key=None, settings=settings, is_utility=True,
    )
    via_alias = await service.resolve_utility_model(session)
    via_real_name = await get_utility_model(session)
    assert via_alias is not None and via_real_name is not None
    assert via_alias.id == via_real_name.id == m.id


async def test_get_utility_model_satisfies_plan_ks_minimal_contract(
    session: AsyncSession, seeded_user: User, settings: Settings
) -> None:
    """Plan K Task 1 contract pin: enrichment/backfill/rolling-summary memory
    (Tasks 6, 7, 9) discover the designated utility model via
    `models_service.resolve_utility_model` (see `test_alias_resolves_same_model`),
    which re-exports this exact function, treating None as 'skip silently'
    per spec D5/D6. This pins the Model | None return shape Plan K's future
    callers depend on."""
    ctx = super_ctx(seeded_user)
    resolved_before = await get_utility_model(session)
    assert resolved_before is None
    m = await _make(session, ctx, settings, "utility-candidate")
    await service.update_model(
        session, ctx, m.id, display_name=None, base_url=None, enabled=None,
        api_key=None, settings=settings, is_utility=True,
    )
    resolved = await get_utility_model(session)
    assert isinstance(resolved, Model)
    assert resolved.id == m.id
