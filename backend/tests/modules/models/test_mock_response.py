import json
from pathlib import Path

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from ragz.core.config import Settings
from ragz.modules.auth.models import User
from ragz.modules.models.service import create_model
from ragz.modules.models.sync import sync_models_to_litellm
from ragz.modules.secrets.crypto import ensure_kek
from ragz.modules.tenancy.context import TenantContext


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

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, bytes]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append((request.method, request.url.path, request.content))
        if request.url.path == "/v1/model/info":
            return httpx.Response(200, json={"data": []})
        return httpx.Response(200, json={})

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)


async def test_mock_response_forwarded_to_litellm_params(
    session: AsyncSession, seeded_user: User, settings: Settings
) -> None:
    """A model with mock_response='pong' produces litellm_params containing
    mock_response='pong' (assert via the recorded /model/new payload on the
    existing mock transport fixture)."""
    ctx = super_ctx(seeded_user)
    await create_model(
        session, ctx, litellm_model_name="loadtest-mock", display_name="Mock",
        provider_kind="openai_compatible", base_url="http://mock.invalid",
        api_key=None, mock_response="pong", settings=settings,
    )
    rec = Recorder()
    await sync_models_to_litellm(session, settings, transport=rec.transport)
    new_calls = [c for c in rec.calls if c[1] == "/model/new"]
    assert len(new_calls) == 1
    payload = json.loads(new_calls[0][2])
    assert payload["litellm_params"]["mock_response"] == "pong"


async def test_mock_response_absent_when_unset(
    session: AsyncSession, seeded_user: User, settings: Settings
) -> None:
    ctx = super_ctx(seeded_user)
    await create_model(
        session, ctx, litellm_model_name="real-model", display_name="Real",
        provider_kind="openai", base_url=None, api_key=None,
        mock_response=None, settings=settings,
    )
    rec = Recorder()
    await sync_models_to_litellm(session, settings, transport=rec.transport)
    new_calls = [c for c in rec.calls if c[1] == "/model/new"]
    assert len(new_calls) == 1
    payload = json.loads(new_calls[0][2])
    assert "mock_response" not in payload["litellm_params"]
