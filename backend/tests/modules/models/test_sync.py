import json
from pathlib import Path

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import raghub
from raghub.core.config import Settings
from raghub.core.errors import UpstreamError
from raghub.modules.auth.models import User
from raghub.modules.models.service import create_model, list_models, update_model
from raghub.modules.models.sync import sync_models_to_litellm
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


class Recorder:
    """The ONE sanctioned mock: LiteLLM's HTTP surface at the httpx layer."""

    def __init__(self, deployed_ids: list[str] | None = None, fail: bool = False) -> None:
        self.deployed_ids = deployed_ids or []
        self.fail = fail
        self.calls: list[tuple[str, str, bytes]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append((request.method, request.url.path, request.content))
        if self.fail:
            return httpx.Response(500, json={"error": "boom"})
        if request.url.path == "/v1/model/info":
            data = [{"model_info": {"id": i}} for i in self.deployed_ids]
            return httpx.Response(200, json={"data": data})
        return httpx.Response(200, json={})

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)


async def seed_two_models(
    session: AsyncSession, user: User, settings: Settings
) -> None:
    ctx = super_ctx(user)
    await create_model(session, ctx, litellm_model_name="gpt-4o-mini",
                       display_name="GPT", provider_kind="openai", base_url=None,
                       api_key="sk-live-777", settings=settings)
    m2 = await create_model(session, ctx, litellm_model_name="llama3",
                            display_name="Llama", provider_kind="ollama",
                            base_url="http://ollama:11434", api_key=None, settings=settings)
    await update_model(session, ctx, m2.id, display_name=None, base_url=None,
                       enabled=False, api_key=None, settings=settings)


async def test_replay_deletes_then_deploys_enabled_only(
    session: AsyncSession, seeded_user: User, settings: Settings
) -> None:
    await seed_two_models(session, seeded_user, settings)
    rec = Recorder(deployed_ids=["stale-a", "stale-b"])
    count = await sync_models_to_litellm(session, settings, transport=rec.transport)
    assert count == 1  # llama3 is disabled
    paths = [(m, p) for m, p, _ in rec.calls]
    assert paths == [
        ("GET", "/v1/model/info"),
        ("POST", "/model/delete"),
        ("POST", "/model/delete"),
        ("POST", "/model/new"),
    ]
    new_payload = json.loads(rec.calls[-1][2])
    assert new_payload["model_name"] == "gpt-4o-mini"
    assert new_payload["litellm_params"]["model"] == "openai/gpt-4o-mini"
    assert new_payload["litellm_params"]["api_key"] == "sk-live-777"  # decrypted only here
    statuses = {m.litellm_model_name: m.sync_status for m in await list_models(session)}
    assert statuses == {"gpt-4o-mini": "synced", "llama3": "synced"}  # uniform outcome


async def test_replay_is_idempotent(
    session: AsyncSession, seeded_user: User, settings: Settings
) -> None:
    await seed_two_models(session, seeded_user, settings)
    rec = Recorder()
    assert await sync_models_to_litellm(session, settings, transport=rec.transport) == 1
    assert await sync_models_to_litellm(session, settings, transport=rec.transport) == 1


async def test_proxy_failure_maps_to_upstream_error(
    session: AsyncSession, seeded_user: User, settings: Settings
) -> None:
    await seed_two_models(session, seeded_user, settings)
    with pytest.raises(UpstreamError):
        await sync_models_to_litellm(
            session, settings, transport=Recorder(fail=True).transport
        )
    assert {m.sync_status for m in await list_models(session)} == {"error"}


def test_decryption_has_exactly_one_caller() -> None:
    """Iron rule 3 guard: _get_secret_decrypted appears only in its module and sync.py."""
    src_root = Path(raghub.__file__).parent
    allowed = {
        src_root / "modules" / "secrets" / "service.py",
        src_root / "modules" / "models" / "sync.py",
    }
    offenders = [
        str(p)
        for p in src_root.rglob("*.py")
        if "_get_secret_decrypted" in p.read_text(encoding="utf-8") and p not in allowed
    ]
    assert offenders == []
