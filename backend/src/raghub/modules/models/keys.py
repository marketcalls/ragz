"""Per-user LiteLLM virtual keys (MODEL-2 + QUOTA-3 gateway backstop).

Iron rule 3 note: THIRD sanctioned caller of secrets._get_secret_decrypted —
outbound gateway authentication, decrypt-in-memory, use-immediately, never
returned to clients or logs. Named in the allowlist test.
"""

from uuid import UUID

import httpx
import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from raghub.core.config import Settings
from raghub.core.errors import NotFoundError
from raghub.modules.secrets import service as secrets_service

_VKEY_PREFIX = "vkey:"


def _max_budget(monthly_tokens: int | None, settings: Settings) -> float | None:
    if monthly_tokens is None or settings.litellm_usd_per_million_tokens <= 0:
        return None
    return round(monthly_tokens / 1_000_000 * settings.litellm_usd_per_million_tokens, 4)


async def get_or_create_user_virtual_key(
    session: AsyncSession,
    settings: Settings,
    *,
    user_id: UUID,
    monthly_tokens: int | None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> str | None:
    name = f"{_VKEY_PREFIX}{user_id}"
    try:
        return await secrets_service._get_secret_decrypted(  # noqa: SLF001
            session, name=name, settings=settings
        )
    except NotFoundError:
        pass
    payload: dict[str, object] = {
        "key_alias": f"raghub-user-{user_id}",
        "user_id": str(user_id),
        "budget_duration": "30d",
    }
    budget = _max_budget(monthly_tokens, settings)
    if budget is not None:
        payload["max_budget"] = budget
    headers = {"Authorization": f"Bearer {settings.litellm_master_key}"}
    try:
        async with httpx.AsyncClient(
            base_url=settings.litellm_url, headers=headers,
            transport=transport, timeout=15.0,
        ) as client:
            resp = await client.post("/key/generate", json=payload)
            resp.raise_for_status()
            key = str(resp.json()["key"])
    except (httpx.HTTPError, KeyError):
        structlog.get_logger().warning("virtual_key_generate_failed", user_id=str(user_id))
        return None  # caller falls back to the master key; app pre-flight still enforces
    await secrets_service.set_secret(
        session, actor_id=None, name=name, value=key, settings=settings
    )
    return key


async def update_user_budget(
    session: AsyncSession,
    settings: Settings,
    *,
    user_id: UUID,
    monthly_tokens: int | None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> None:
    """Best-effort budget mirror after a quota change; no key yet -> no-op."""
    try:
        key = await secrets_service._get_secret_decrypted(  # noqa: SLF001
            session, name=f"{_VKEY_PREFIX}{user_id}", settings=settings
        )
    except NotFoundError:
        return
    headers = {"Authorization": f"Bearer {settings.litellm_master_key}"}
    try:
        async with httpx.AsyncClient(
            base_url=settings.litellm_url, headers=headers,
            transport=transport, timeout=15.0,
        ) as client:
            resp = await client.post(
                "/key/update",
                json={"key": key, "max_budget": _max_budget(monthly_tokens, settings)},
            )
            resp.raise_for_status()
    except httpx.HTTPError:
        structlog.get_logger().warning("virtual_key_budget_update_failed",
                                       user_id=str(user_id))
