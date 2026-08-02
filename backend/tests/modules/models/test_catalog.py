from datetime import timedelta
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ragz.core.config import Settings
from ragz.core.db import naive_utc
from ragz.modules.models.catalog import ModelCatalogEntry, refresh_catalog
from ragz.modules.secrets.crypto import ensure_kek

TWO_MODEL_JSON = {
    "gpt-4o-mini": {
        "litellm_provider": "openai",
        "mode": "chat",
        "max_input_tokens": 128000,
        "input_cost_per_token": 0.00000015,
        "output_cost_per_token": 0.0000006,
    },
    "claude-3-haiku": {
        "litellm_provider": "anthropic",
        "mode": "chat",
        "max_input_tokens": 200000,
        "input_cost_per_token": 0.00000025,
        "output_cost_per_token": 0.00000125,
    },
    "sample_spec": {"litellm_provider": "ignore-me"},
}


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    kek = tmp_path / "kek"
    ensure_kek(str(kek))
    return Settings(_env_file=None, kek_file=str(kek))


class Recorder:
    """The ONE sanctioned mock: the remote catalog's HTTP surface."""

    def __init__(self, *, fail: bool = False, data: dict[str, object] | None = None) -> None:
        self.fail = fail
        self.data = data if data is not None else TWO_MODEL_JSON
        self.requests: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(str(request.url))
        if self.fail:
            raise httpx.ConnectError("boom")
        return httpx.Response(200, json=self.data)

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)


async def test_remote_fetch_upserts(session: AsyncSession, settings: Settings) -> None:
    rec = Recorder()
    n = await refresh_catalog(session, settings, transport=rec.transport)
    assert n == 2
    rows = {
        r.name: r for r in (await session.execute(select(ModelCatalogEntry))).scalars()
    }
    assert set(rows) == {"gpt-4o-mini", "claude-3-haiku"}
    gpt = rows["gpt-4o-mini"]
    assert gpt.source == "remote"
    assert gpt.provider == "openai"
    assert gpt.max_input_tokens == 128000
    assert gpt.input_cost_per_token == 0.00000015
    assert gpt.output_cost_per_token == 0.0000006
    # Position pins JSON enumeration order (LiteLLM appends new models at the
    # END of its JSON, so higher position ~= newer release).
    assert gpt.position == 0
    assert rows["claude-3-haiku"].position == 1


async def test_cache_window_respected(session: AsyncSession, settings: Settings) -> None:
    rec = Recorder()
    assert await refresh_catalog(session, settings, transport=rec.transport) == 2
    assert len(rec.requests) == 1

    # Within the 3-day cache window: no HTTP call, nothing upserted.
    n = await refresh_catalog(session, settings, transport=rec.transport)
    assert n == 0
    assert len(rec.requests) == 1

    # force=True bypasses the cache.
    n = await refresh_catalog(session, settings, transport=rec.transport, force=True)
    assert n == 2
    assert len(rec.requests) == 2


async def test_snapshot_fallback_on_network_failure(
    session: AsyncSession, settings: Settings
) -> None:
    rec = Recorder(fail=True)
    n = await refresh_catalog(session, settings, transport=rec.transport)
    assert n > 0
    rows = list((await session.execute(select(ModelCatalogEntry))).scalars())
    assert rows and all(r.source == "snapshot" for r in rows)
    # Snapshot fallback gets the same position stamping as the remote path.
    assert sorted(r.position for r in rows) == list(range(len(rows)))


async def test_airgap_url_empty_never_fetches(session: AsyncSession, settings: Settings) -> None:
    settings.model_catalog_url = ""
    rec = Recorder()
    n = await refresh_catalog(session, settings, transport=rec.transport)
    assert n > 0
    assert rec.requests == []
    rows = list((await session.execute(select(ModelCatalogEntry))).scalars())
    assert all(r.source == "snapshot" for r in rows)


async def test_stale_rows_kept_on_second_failure(
    session: AsyncSession, settings: Settings
) -> None:
    """A prior successful fetch exists; a later failed refresh (cache expired)
    must not clobber the existing rows with the bundled snapshot."""
    rec = Recorder()
    await refresh_catalog(session, settings, transport=rec.transport)
    # Age the cached rows past the 3-day window without a network dependency.
    for row in (await session.execute(select(ModelCatalogEntry))).scalars():
        row.fetched_at = naive_utc() - timedelta(days=4)
    await session.commit()

    fail_rec = Recorder(fail=True)
    n = await refresh_catalog(session, settings, transport=fail_rec.transport)
    assert n == 0
    rows = {
        r.name: r for r in (await session.execute(select(ModelCatalogEntry))).scalars()
    }
    assert set(rows) == {"gpt-4o-mini", "claude-3-haiku"}
    assert all(r.source == "remote" for r in rows.values())


async def test_degenerate_empty_object_body_does_not_wipe_catalog(
    session: AsyncSession, settings: Settings
) -> None:
    """A 200 response that parses as JSON but yields zero usable rows (e.g. an
    empty object, or an upstream schema change) must never wipe existing rows."""
    rec = Recorder()
    await refresh_catalog(session, settings, transport=rec.transport)
    for row in (await session.execute(select(ModelCatalogEntry))).scalars():
        row.fetched_at = naive_utc() - timedelta(days=4)
    await session.commit()

    empty_rec = Recorder(data={})
    n = await refresh_catalog(session, settings, transport=empty_rec.transport)
    assert n == 0
    rows = {
        r.name: r for r in (await session.execute(select(ModelCatalogEntry))).scalars()
    }
    assert set(rows) == {"gpt-4o-mini", "claude-3-haiku"}
    assert all(r.source == "remote" for r in rows.values())


async def test_degenerate_json_array_body_does_not_crash_or_wipe_catalog(
    session: AsyncSession, settings: Settings
) -> None:
    """A 200 response whose body is a JSON array (not an object) must degrade
    gracefully instead of raising AttributeError out of `_rows`."""
    rec = Recorder()
    await refresh_catalog(session, settings, transport=rec.transport)
    for row in (await session.execute(select(ModelCatalogEntry))).scalars():
        row.fetched_at = naive_utc() - timedelta(days=4)
    await session.commit()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=["not", "a", "dict"])

    n = await refresh_catalog(
        session, settings, transport=httpx.MockTransport(handler)
    )
    assert n == 0
    rows = {
        r.name: r for r in (await session.execute(select(ModelCatalogEntry))).scalars()
    }
    assert set(rows) == {"gpt-4o-mini", "claude-3-haiku"}
    assert all(r.source == "remote" for r in rows.values())
