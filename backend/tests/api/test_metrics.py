"""Prometheus exposition and instrumentation (Phase 3 item 1).

The two properties under test are the ones that hurt in production if wrong:
label cardinality (a per-UUID label set melts the scrape target) and exposure
(metrics are operational intelligence, not a liveness bit).
"""

import dataclasses

import pytest

from ragz.core.config import Settings, get_settings


def _with_token(settings: Settings, token: str) -> Settings:
    return dataclasses.replace(settings, metrics_token=token) if dataclasses.is_dataclass(
        settings
    ) else settings.model_copy(update={"metrics_token": token})


async def test_metrics_is_404_when_no_token_is_configured(client, test_settings) -> None:  # type: ignore[no-untyped-def]
    """Off by default. 404 rather than 401 so a scanner cannot tell a
    deployment with metrics-but-no-credential from one without the endpoint."""
    r = await client.get("/metrics")
    assert r.status_code == 404


async def test_metrics_requires_the_bearer_token(client, test_settings) -> None:  # type: ignore[no-untyped-def]
    client._transport.app.dependency_overrides[get_settings] = lambda: _with_token(  # noqa: SLF001
        test_settings, "s3cret"
    )
    assert (await client.get("/metrics")).status_code == 404
    assert (
        await client.get("/metrics", headers={"Authorization": "Bearer wrong"})
    ).status_code == 404
    assert (
        await client.get("/metrics", headers={"Authorization": "Basic s3cret"})
    ).status_code == 404

    ok = await client.get("/metrics", headers={"Authorization": "Bearer s3cret"})
    assert ok.status_code == 200
    assert "text/plain" in ok.headers["content-type"]
    assert b"ragz_http_requests_total" in ok.content


async def test_http_metrics_label_by_route_template_not_by_path(
    client, test_settings
) -> None:  # type: ignore[no-untyped-def]
    """The cardinality guarantee. A UUID in the path must NOT reach a label
    value -- otherwise every document minted its own time series."""
    doc_id = "11111111-2222-3333-4444-555555555555"
    await client.get(f"/api/v1/documents/{doc_id}")  # 401/404 is fine; it routes

    client._transport.app.dependency_overrides[get_settings] = lambda: _with_token(  # noqa: SLF001
        test_settings, "s3cret"
    )
    body = (
        await client.get("/metrics", headers={"Authorization": "Bearer s3cret"})
    ).content.decode()

    assert doc_id not in body, "raw path id leaked into a metric label"
    # The FULL template, prefix included. Asserting only "{document_id}" would
    # pass against the sub-path "/documents/{document_id}" that
    # scope["route"].path actually returns -- which silently merges every
    # router sharing a sub-path into one series. See route_template.
    assert 'route="/api/v1/documents/{document_id}"' in body


async def test_unmatched_requests_collapse_to_one_series(client, test_settings) -> None:  # type: ignore[no-untyped-def]
    """A 404 for an arbitrary URL must not create a series per URL -- that is
    the same cardinality blow-up, reachable by anyone who can send requests."""
    for path in ("/nope/one", "/nope/two", "/nope/three"):
        await client.get(path)

    client._transport.app.dependency_overrides[get_settings] = lambda: _with_token(  # noqa: SLF001
        test_settings, "s3cret"
    )
    body = (
        await client.get("/metrics", headers={"Authorization": "Bearer s3cret"})
    ).content.decode()

    assert "/nope/one" not in body and "/nope/three" not in body
    assert 'route="unmatched"' in body


async def test_healthz_is_recorded_with_its_template_and_status(
    client, test_settings
) -> None:  # type: ignore[no-untyped-def]
    await client.get("/healthz")
    client._transport.app.dependency_overrides[get_settings] = lambda: _with_token(  # noqa: SLF001
        test_settings, "s3cret"
    )
    body = (
        await client.get("/metrics", headers={"Authorization": "Bearer s3cret"})
    ).content.decode()
    assert 'route="/healthz"' in body
    assert 'status="200"' in body


def test_observe_stage_records_even_when_the_stage_raises() -> None:
    """A stage that times out is exactly the latency an operator is hunting
    for, so the observation must not be skipped on the exception path."""
    from prometheus_client import REGISTRY

    from ragz.core.metrics import observe_stage

    def _count() -> float:
        v = REGISTRY.get_sample_value(
            "ragz_retrieval_stage_duration_seconds_count", {"stage": "unit_test_stage"}
        )
        return v or 0.0

    before = _count()
    with pytest.raises(RuntimeError):
        with observe_stage("unit_test_stage"):
            raise RuntimeError("stage blew up")
    assert _count() == before + 1
