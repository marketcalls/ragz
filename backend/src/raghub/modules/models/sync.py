"""Idempotent full-config replay to the LiteLLM proxy management API (spec 3.5).

Replace-all strategy: list deployed models, delete each, re-create from the
enabled registry rows. Runs after every registry CRUD (route layer) and on app
startup, so the proxy's own store is disposable state.

Iron rule 3 note: `_get_secret_decrypted` is defined in secrets/service.py and
called from exactly four files — this module, auth/oidc.py, and
models/keys.py, plus its own definition. A source-scan test
(tests/modules/models/test_sync.py::test_decryption_callers_are_exactly_the_gateway_allowlist)
pins that allowlist; adding a caller is a security review event, not a
refactor.
"""

from typing import Any

import httpx
import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from raghub.core.config import Settings
from raghub.core.errors import NotFoundError, UpstreamError
from raghub.modules.models.models import Model
from raghub.modules.models.service import list_enabled_models, list_models
from raghub.modules.secrets import service as secrets_service


async def _litellm_params(
    session: AsyncSession, model: Model, settings: Settings
) -> dict[str, Any]:
    params: dict[str, Any] = {}
    if model.provider_kind == "ollama":
        params["model"] = f"ollama/{model.litellm_model_name}"
        params["api_base"] = model.base_url
    elif model.provider_kind == "litellm":
        # Catalog names already carry their provider prefix for non-openai
        # providers (e.g. gemini/gemini-2.5-pro) — pass VERBATIM, no api_base.
        params["model"] = model.litellm_model_name
    else:  # openai | openai_compatible both speak the OpenAI protocol
        params["model"] = f"openai/{model.litellm_model_name}"
        if model.provider_kind == "openai_compatible":
            params["api_base"] = model.base_url
    try:
        params["api_key"] = await secrets_service._get_secret_decrypted(  # noqa: SLF001
            session, name=f"model:{model.id}", settings=settings
        )
    except NotFoundError:
        pass  # keyless provider (ollama / open local endpoints)
    if model.mock_response:
        params["mock_response"] = model.mock_response
    return params


async def sync_models_to_litellm(
    session: AsyncSession,
    settings: Settings,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> int:
    """Replace LiteLLM's deployed models with the enabled registry. Returns count deployed.

    Persists the outcome on every registry row (sync_status: synced|error) - the
    replay is all-or-nothing, so the outcome is uniform.
    """
    models = await list_enabled_models(session)
    all_models = await list_models(session)
    headers = {"Authorization": f"Bearer {settings.litellm_master_key}"}
    limits = httpx.Limits(
        max_connections=settings.httpx_max_connections,
        max_keepalive_connections=settings.httpx_max_keepalive,
    )
    try:
        async with httpx.AsyncClient(
            base_url=settings.litellm_url, headers=headers,
            transport=transport, timeout=30.0, limits=limits,
        ) as client:
            try:
                info = await client.get("/v1/model/info")
                info.raise_for_status()
                deployed_models = info.json().get("data", [])
            except httpx.HTTPStatusError:
                # A fresh/empty proxy returns 500 ("LLM Model List not loaded")
                # when it has no models - nothing to delete, so treat as empty
                # and continue the replay instead of failing the first sync.
                structlog.get_logger().info(
                    "litellm_model_list_unavailable, treating as empty"
                )
                deployed_models = []
            for deployed in deployed_models:
                deployed_id = deployed.get("model_info", {}).get("id")
                if deployed_id:
                    r = await client.post("/model/delete", json={"id": deployed_id})
                    r.raise_for_status()
            for model in models:
                payload = {
                    "model_name": model.litellm_model_name,
                    "litellm_params": await _litellm_params(session, model, settings),
                }
                r = await client.post("/model/new", json=payload)
                r.raise_for_status()
    except httpx.HTTPError as exc:
        for model in all_models:
            model.sync_status = "error"
        await session.commit()
        raise UpstreamError("LiteLLM sync failed") from exc
    for model in all_models:
        model.sync_status = "synced"
    await session.commit()
    return len(models)
