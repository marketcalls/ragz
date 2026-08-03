import json
from pathlib import Path

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import ragz
from ragz.core.config import Settings
from ragz.core.errors import UpstreamError
from ragz.modules.auth.models import User
from ragz.modules.models.service import create_model, list_models, update_model
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
    # tests/conftest.py's `engine` fixture seeds one globally-present tei
    # model ("local-embeddings") that's excluded from replay (see
    # test_tei_model_excluded_from_litellm_sync) and so keeps its seeded
    # sync_status="synced" untouched.
    assert statuses == {
        "gpt-4o-mini": "synced", "llama3": "synced", "local-embeddings": "synced",
    }  # uniform outcome


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


async def test_litellm_kind_passes_catalog_name_verbatim(
    session: AsyncSession, seeded_user: User, settings: Settings
) -> None:
    """provider_kind="litellm": catalog names for non-openai providers already
    carry their provider prefix (e.g. gemini/gemini-2.5-pro), so the replay
    must pass litellm_model_name VERBATIM — no openai/ or ollama/ prefixing,
    no api_base — with the stored key attached as usual."""
    ctx = super_ctx(seeded_user)
    await create_model(session, ctx, litellm_model_name="gemini/gemini-2.5-pro",
                       display_name="Gemini 2.5 Pro", provider_kind="litellm",
                       base_url=None, api_key="AIza-live-42", settings=settings)
    rec = Recorder()
    assert await sync_models_to_litellm(session, settings, transport=rec.transport) == 1
    new_payload = json.loads(rec.calls[-1][2])
    assert new_payload["model_name"] == "gemini/gemini-2.5-pro"
    assert new_payload["litellm_params"]["model"] == "gemini/gemini-2.5-pro"
    assert new_payload["litellm_params"]["api_key"] == "AIza-live-42"
    assert "api_base" not in new_payload["litellm_params"]


async def test_sync_tolerates_empty_litellm_proxy_on_first_sync(
    session: AsyncSession, seeded_user: User, settings: Settings
) -> None:
    """A fresh LiteLLM proxy with zero models returns 500 on GET /v1/model/info
    ("LLM Model List not loaded"). The replay must treat that as an empty
    deployed list and still register the first model, rather than failing."""
    ctx = super_ctx(seeded_user)
    await create_model(session, ctx, litellm_model_name="gpt-4o-mini",
                       display_name="GPT", provider_kind="openai", base_url=None,
                       api_key="sk-live-777", settings=settings)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/model/info":
            return httpx.Response(
                500,
                json={"detail": {"error": "LLM Model List not loaded in ..."}},
            )
        return httpx.Response(200, json={})

    count = await sync_models_to_litellm(
        session, settings, transport=httpx.MockTransport(handler)
    )
    assert count == 1
    statuses = {m.litellm_model_name: m.sync_status for m in await list_models(session)}
    # The globally-seeded tei model ("local-embeddings", see
    # tests/conftest.py's `engine` fixture) is excluded from replay and keeps
    # its seeded sync_status="synced".
    assert statuses == {"gpt-4o-mini": "synced", "local-embeddings": "synced"}


async def test_tei_model_excluded_from_litellm_sync(
    session: AsyncSession, seeded_user: User, settings: Settings
) -> None:
    """DOC-10: the local TEI embedding model is never routed through the
    LiteLLM gateway -- it must never appear in a /model/new payload."""
    ctx = super_ctx(seeded_user)
    await create_model(
        session, ctx, litellm_model_name="local-embeddings-2", display_name="Local 2",
        provider_kind="tei", base_url=None, api_key=None, settings=settings,
        modality="embedding", dimension=384,
    )
    await create_model(
        session, ctx, litellm_model_name="gpt-4o-mini", display_name="GPT",
        provider_kind="openai", base_url=None, api_key="sk-live-777", settings=settings,
    )
    rec = Recorder()
    count = await sync_models_to_litellm(session, settings, transport=rec.transport)
    assert count == 1  # the tei model is excluded, not just disabled
    new_payloads = [
        json.loads(content) for method, path, content in rec.calls if path == "/model/new"
    ]
    assert [p["model_name"] for p in new_payloads] == ["gpt-4o-mini"]


def test_decryption_callers_are_exactly_the_gateway_allowlist() -> None:
    """Iron rule 3 guard: _get_secret_decrypted appears ONLY in its own module
    and the sanctioned gateway-boundary callers. Phase 2 addition: auth/oidc.py
    (OIDC client secret -> one outbound token request; same decrypt-in-memory,
    use-immediately, never-return pattern as sync.py). Adding a caller here is
    a security review event, not a refactor.
    models/keys.py: per-user LiteLLM virtual keys — outbound gateway auth.
    chat/web.py: Tavily web-search key (Phase 3 D7) — same decrypt-in-memory,
    use-immediately pattern; the ONLY allowlist change in Phase 3.
    retrieval/rerank.py: Cohere reranker key — decrypt-in-memory, use-immediately
    outbound rerank call.
    documents/parsers.py: LlamaParse key — decrypt-in-memory for one outbound
    parse call."""
    src_root = Path(ragz.__file__).parent
    allowed = {
        src_root / "modules" / "secrets" / "service.py",
        src_root / "modules" / "models" / "sync.py",
        src_root / "modules" / "auth" / "oidc.py",
        src_root / "modules" / "models" / "keys.py",
        src_root / "modules" / "chat" / "web.py",
        src_root / "modules" / "retrieval" / "rerank.py",
        src_root / "modules" / "documents" / "parsers.py",
    }
    offenders = [
        str(p)
        for p in src_root.rglob("*.py")
        if "_get_secret_decrypted" in p.read_text(encoding="utf-8") and p not in allowed
    ]
    assert offenders == []
