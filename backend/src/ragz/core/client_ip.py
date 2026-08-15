"""Real-client-IP resolution behind a reverse proxy (sec RAGZ-PUB-06
follow-up).

`request.client.host` is the IMMEDIATE TCP peer. Behind any reverse proxy or
load balancer (nginx, an ALB, a CDN) that peer is the proxy itself, not the
browser/API caller -- so every real client collapses onto ONE IP for any
per-IP control (rate limiting, IP-keyed logging). The `X-Forwarded-For`
header carries the original chain, but it is attacker-controlled: a caller
with no proxy in front of it can set that header to anything it likes.

`client_ip()` below is the ONE function that resolves a request's real
client IP (mirrors iron rule 1's "one code path per store", applied to
inbound IP trust instead of tenant data). The policy is deterministic and
spoof-resistant:

- `Settings.trusted_proxies` empty (the default) -> always return the direct
  peer. XFF is never even read. This is exactly the pre-existing behavior,
  so a fresh install / dev / test needs zero configuration to keep working.
- Peer is NOT one of `trusted_proxies` -> return the peer, and IGNORE any
  `X-Forwarded-For` header outright. This is the spoof-resistance guarantee:
  XFF is only ever honored from a hop this deployment itself controls.
- Peer IS a trusted proxy -> parse `X-Forwarded-For` (comma list, left =
  original client ... right = nearest hop) and walk it from the RIGHT,
  skipping any hop that is itself a trusted proxy (a chain can have more
  than one proxy in front), returning the first hop that isn't. That is the
  real client. If XFF is absent, empty, or every hop in it is itself a
  trusted proxy (or malformed), fall back to the peer.

PRODUCTION DEPLOYMENTS BEHIND A REVERSE PROXY/CDN MUST SET
`RAGZ_TRUSTED_PROXIES` to that proxy's actual egress IPs/CIDRs. Leaving it
empty behind a real proxy silently rate-limits (and IP-logs) the proxy's one
IP for every tenant's traffic; setting it too broad (e.g. `0.0.0.0/0`)
reintroduces the exact spoofing this module exists to prevent.
"""

from __future__ import annotations

import ipaddress
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import Request

    from ragz.core.config import Settings

_IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network


def _parse_ip(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """Never raises -- an unparseable address is simply "not an address"."""
    try:
        return ipaddress.ip_address(value.strip())
    except ValueError:
        return None


def _trusted_networks(trusted_proxies: list[str]) -> list[_IPNetwork]:
    """Parse configured trusted-proxy entries into networks, silently
    dropping anything malformed -- a bad config entry must never crash
    request handling; it just fails to match (and so is never trusted)."""
    networks: list[_IPNetwork] = []
    for raw in trusted_proxies:
        raw = raw.strip()
        if not raw:
            continue
        try:
            networks.append(ipaddress.ip_network(raw, strict=False))
        except ValueError:
            continue
    return networks


def _is_trusted(ip_text: str, networks: list[_IPNetwork]) -> bool:
    addr = _parse_ip(ip_text)
    if addr is None:
        return False
    return any(addr in network for network in networks)


def client_ip(request: Request, settings: Settings) -> str:
    """Resolve the real client IP for `request` per the policy documented
    above. Never raises -- malformed input at any stage (missing peer,
    missing/garbage XFF, unparseable config entries) degrades to the safest
    available fallback (the direct peer) rather than erroring out of request
    handling."""
    peer = request.client.host if request.client else "unknown"

    trusted_proxies = settings.trusted_proxies
    if not trusted_proxies:
        return peer  # trust nobody -- dev-safe default, identical to pre-existing behavior

    networks = _trusted_networks(trusted_proxies)
    if not networks or not _is_trusted(peer, networks):
        # Direct peer isn't a trusted hop (or every configured entry was
        # malformed) -- XFF is spoofable by definition from here, so it is
        # never consulted. This is the spoof-resistance guarantee.
        return peer

    xff = request.headers.get("x-forwarded-for", "")
    hops = [h.strip() for h in xff.split(",") if h.strip()]
    if not hops:
        return peer  # trusted proxy but no/empty XFF -- fall back to the peer

    # Walk right-to-left (nearest hop first, per standard XFF ordering).
    # Skip hops that are themselves trusted proxies (a chain can have more
    # than one in front of this app) and return the first hop that is NOT --
    # deterministically, the real client.
    for hop in reversed(hops):
        if _parse_ip(hop) is None:
            continue  # malformed entry -- skip, never raise
        if not _is_trusted(hop, networks):
            return hop

    # Every hop was itself a trusted proxy (or malformed) -- no real client
    # address survived the walk. Documented choice: fall back to the peer
    # (the nearest hop this deployment itself terminated) rather than
    # guessing at the leftmost XFF entry, which is unverified and, from a
    # multi-hop chain, could itself have been supplied by whatever sits in
    # front of the outermost trusted proxy.
    return peer
