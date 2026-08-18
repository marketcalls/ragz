"""Liveness/readiness probes for orchestrators (k8s-style /healthz, /readyz).
Deliberately unauthenticated -- orchestrators poll these without credentials,
so no `require_action`/session-cookie gate belongs here."""

import secrets
import time
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy import text

from ragz.core.config import Settings, get_settings
from ragz.core.errors import NotFoundError

router = APIRouter(tags=["health"])

#: Same injection style as the auth routes, so a test can override it via
#: app.dependency_overrides rather than mutating process env behind an
#: lru_cache.
SettingsDep = Annotated[Settings, Depends(get_settings)]


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    """Pure in-memory liveness check -- no DB dependency at all, so this can
    never contend for a pool connection regardless of request volume."""
    return {"status": "ok"}


# RAGZ-PUB-03: /readyz previously ran `SELECT 1` on a freshly checked-out
# pooled DB session for EVERY request (via `Depends(get_session)`, resolved
# before the handler body even ran). /readyz is unauthenticated by design
# (orchestrators probe it with no credentials), so a flood of anonymous
# requests -- accidental or a deliberate DoS -- could check out one of the
# pool's 10+20 connections per request and starve real tenant traffic doing
# actual work, for a check whose answer barely changes second to second.
#
# Fix: cache the last probe's outcome in-process for a short TTL (monotonic
# clock) so every request inside that window reuses it instead of opening a
# new connection. 5s is short enough that a genuinely down DB is still
# reported not-ready within one TTL window (orchestrator health-check
# intervals are themselves typically >=5s, e.g. k8s' default periodSeconds
# is 10s), and long enough to collapse any realistic request burst -- even
# a sustained flood -- onto at most one DB probe per window. A tiny race
# between two concurrent requests both seeing a stale cache and both firing
# a probe is acceptable (one extra query, not unbounded ones); no lock is
# used to keep this a simple module-level timestamp+result cache with no
# new dependency.
_READYZ_CACHE_TTL_SECONDS = 5.0

# (monotonic timestamp of the last probe, whether it succeeded) or None
# before the first request.
_readyz_cache: tuple[float, bool] | None = None


async def _probe_db(request: Request) -> bool:
    try:
        factory = request.app.state.session_factory
        async with factory() as session:
            await session.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001 - readiness must never 500, just report unavailable
        return False
    return True


@router.get("/readyz")
async def readyz(request: Request) -> JSONResponse:
    global _readyz_cache
    now = time.monotonic()
    if _readyz_cache is None or (now - _readyz_cache[0]) >= _READYZ_CACHE_TTL_SECONDS:
        _readyz_cache = (now, await _probe_db(request))
    _, is_ready = _readyz_cache
    if not is_ready:
        return JSONResponse(status_code=503, content={"status": "unavailable"})
    return JSONResponse(content={"status": "ready"})


@router.get("/metrics", include_in_schema=False)
async def metrics(request: Request, settings: SettingsDep) -> Response:
    """Prometheus exposition (Phase 3 item 1).

    Unlike /healthz and /readyz above, this is NOT unauthenticated. It reports
    route inventory, traffic volumes and error rates, which is operational
    intelligence rather than a liveness bit. Disabled entirely when
    RAGZ_METRICS_TOKEN is unset, so a deployment cannot acquire an open metrics
    endpoint by accident.

    404 (not 401) when disabled: an unconfigured endpoint should be
    indistinguishable from one that does not exist, so a scanner learns nothing
    about whether this deployment has metrics to find. compare_digest for the
    token check, so a wrong guess cannot be refined by timing.
    """
    from ragz.core.metrics import render

    expected = settings.metrics_token
    if not expected:
        raise NotFoundError("not found")

    header = request.headers.get("authorization", "")
    scheme, _, presented = header.partition(" ")
    if scheme.lower() != "bearer" or not secrets.compare_digest(presented, expected):
        # Also 404 rather than 401: same reasoning, and there is no
        # interactive credential for a caller to be prompted for.
        raise NotFoundError("not found")

    payload, content_type = render()
    return Response(content=payload, media_type=content_type)
