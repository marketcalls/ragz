"""Model catalog sync (MODEL-10/G7): LiteLLM's pricing/context-window JSON.

Remote fetch with a 3-day cache; bundled snapshot fallback keeps air-gapped
installs (model_catalog_url="") working with zero network calls.
"""

import json
from datetime import datetime, timedelta
from importlib import resources
from typing import Any

import httpx
import structlog
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from raghub.core.config import Settings
from raghub.core.db import Base, naive_utc

_CACHE = timedelta(days=3)


class ModelCatalogEntry(Base):
    __tablename__ = "model_catalog"

    name: Mapped[str] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(default="")
    mode: Mapped[str | None] = mapped_column(default=None)
    max_input_tokens: Mapped[int | None] = mapped_column(default=None)
    input_cost_per_token: Mapped[float | None] = mapped_column(default=None)
    output_cost_per_token: Mapped[float | None] = mapped_column(default=None)
    # JSON enumeration order. LiteLLM appends new models at the END of its
    # JSON, so higher position ~= newer release; the picker sorts on it DESC.
    position: Mapped[int] = mapped_column(default=0, server_default="0")
    source: Mapped[str] = mapped_column(default="remote")  # remote | snapshot
    fetched_at: Mapped[datetime] = mapped_column(default=naive_utc)


def _load_snapshot() -> dict[str, Any]:
    raw = (
        resources.files("raghub.modules.models.data")
        .joinpath("model_prices_snapshot.json")
        .read_text()
    )
    return json.loads(raw)  # type: ignore[no-any-return]


def _rows(data: Any, source: str) -> list[ModelCatalogEntry]:
    if not isinstance(data, dict):
        return []
    rows: list[ModelCatalogEntry] = []
    for name, meta in data.items():
        if not isinstance(meta, dict) or name == "sample_spec":
            continue
        rows.append(
            ModelCatalogEntry(
                name=name,
                provider=str(meta.get("litellm_provider", "")),
                mode=meta.get("mode"),
                max_input_tokens=meta.get("max_input_tokens"),
                input_cost_per_token=meta.get("input_cost_per_token"),
                output_cost_per_token=meta.get("output_cost_per_token"),
                position=len(rows),  # enumeration order: higher ~= newer
                source=source,
            )
        )
    return rows


async def refresh_catalog(
    session: AsyncSession,
    settings: Settings,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    force: bool = False,
) -> int:
    newest = (
        await session.execute(select(func.max(ModelCatalogEntry.fetched_at)))
    ).scalar_one_or_none()
    if not force and newest is not None and naive_utc() - newest < _CACHE:
        return 0

    data: dict[str, Any] | None = None
    source = "remote"
    if settings.model_catalog_url:
        try:
            async with httpx.AsyncClient(transport=transport, timeout=30.0) as client:
                resp = await client.get(settings.model_catalog_url)
                resp.raise_for_status()
                data = resp.json()
        except (httpx.HTTPError, ValueError):
            structlog.get_logger().warning("model_catalog_fetch_failed", exc_info=True)

    rows = _rows(data, source) if data is not None else []
    if not rows:
        # Fetch failed outright, or parsed to zero usable rows (empty object,
        # non-dict body, upstream schema change, captive-portal JSON, etc).
        # Either way, treat it like a failure: never wipe good rows with this.
        if newest is not None:
            return 0  # keep stale rows over clobbering them with a bad fetch
        data, source = _load_snapshot(), "snapshot"
        rows = _rows(data, source)

    await session.execute(delete(ModelCatalogEntry))  # full replace: pk-safe, idempotent
    session.add_all(rows)
    await session.commit()
    return len(rows)
