"""Liveness/readiness probes for orchestrators (k8s-style /healthz, /readyz).
Deliberately unauthenticated -- orchestrators poll these without credentials,
so no `require_action`/session-cookie gate belongs here."""

import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

router = APIRouter(tags=["health"])


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
