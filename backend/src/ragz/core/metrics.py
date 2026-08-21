"""Prometheus instrumentation (Phase 3 item 1 of the 2026-08-17 review).

CLAUDE.md listed `ragz_<module>_<metric>` metrics and per-stage RAG latency
histograms under **Not implemented** -- earlier revisions of that file asserted
them as if they existed, which is worse than absence: a reviewer reads the
claim and stops looking for the gap. This module is the first half of closing
it (metrics; tracing is still absent and still marked as such).

Two decisions worth stating, because both are easy to get wrong in a way that
only hurts in production:

**Label cardinality.** HTTP metrics are labelled with the ROUTE TEMPLATE
(`/api/v1/workspaces/{workspace_id}/documents`), never the request path. Paths
in this API carry UUIDs, so labelling by path would mint a fresh time series
per workspace, per document, per chat -- a well-known way to melt a Prometheus
server. Anything without a matched route collapses to a single "unmatched"
series rather than leaking the raw URL.

**Exposure.** /metrics is off unless `RAGZ_METRICS_TOKEN` is set, and requires
it as a bearer token when it is. Metrics are operational intelligence -- route
inventory, traffic volumes, error rates -- so an unauthenticated endpoint would
sit awkwardly beside iron rule 4. Off-by-default means an operator opts in
deliberately rather than inheriting an open endpoint from a deploy they did not
read.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from time import perf_counter

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

#: Buckets tuned for this application rather than the library default, which
#: tops out at 10s. RAG turns legitimately run tens of seconds (a 120s LLM
#: timeout is configured), so the default buckets would lump every slow request
#: into +Inf and make the p99 unreadable exactly when it matters.
_LATENCY_BUCKETS = (
    0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0,
)

http_requests_total = Counter(
    "ragz_http_requests_total",
    "HTTP requests by route template, method and status class.",
    ("method", "route", "status"),
)

http_request_duration_seconds = Histogram(
    "ragz_http_request_duration_seconds",
    "HTTP request latency by route template.",
    ("method", "route"),
    buckets=_LATENCY_BUCKETS,
)

#: The per-stage RAG histogram CLAUDE.md names. One metric with a `stage` label
#: rather than a metric per stage: stages are compared against each other
#: ("where did the turn go?"), and a label keeps that a single query.
retrieval_stage_duration_seconds = Histogram(
    "ragz_retrieval_stage_duration_seconds",
    "Latency of one stage of the retrieval pipeline.",
    ("stage",),
    buckets=_LATENCY_BUCKETS,
)

@contextmanager
def observe_stage(stage: str) -> Iterator[None]:
    """Time a retrieval stage, recording it even when the stage raises.

    Failures are the interesting latencies -- a stage that times out is exactly
    what an operator is looking for -- so this deliberately does NOT skip the
    observation on the exception path.
    """
    started = perf_counter()
    try:
        yield
    finally:
        retrieval_stage_duration_seconds.labels(stage=stage).observe(perf_counter() - started)


def render() -> tuple[bytes, str]:
    """The exposition payload and its content type."""
    return generate_latest(), CONTENT_TYPE_LATEST
