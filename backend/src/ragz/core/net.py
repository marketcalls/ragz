"""Central egress guard for admin/OIDC-configurable outbound targets (sec
RAGZ-PUB-11).

Superadmin-set values that this backend later dials out to -- the OIDC
issuer (discovery/token/JWKS URLs) and the SMTP host/port -- have no
centralized SSRF policy: a privileged-but-malicious or simply misconfigured
value can point at an internal service or the cloud metadata endpoint
(169.254.169.254). This module is the ONE place that answers "is this
outbound target safe to dial" -- mirrors iron rule 1's "one code path per
store" for tenant isolation, applied to egress instead of ingress.

Environment-gated exactly like `core/config.py`'s `_production_fails_closed`:
dev/test stay fully permissive (local services live at localhost/docker
hostnames -- litellm, tei, minio, OIDC test providers) so this module is a
NO-OP outside `environment in ("production", "staging")`. Callers still
invoke `assert_public_url`/`assert_public_host` unconditionally -- the
environment check lives in here, once, not scattered across call sites.

DNS-rebinding caveat: both functions validate the resolved IP(s) AT CHECK
TIME, before the caller's own client (httpx, aiosmtplib) independently
re-resolves the same host and connects. A host whose DNS record answers with
a public IP here and a private one moments later (TOCTOU) would slip past
this check -- closing that gap requires pinning the validated IP through the
actual connection (a custom transport/connector per client), which is out of
scope for stdlib httpx/aiosmtplib without deeper client surgery. Track a
pinned-connector follow-up; blocking the resolved IP here remains the
primary control, not the only one that should ever exist.
"""

import asyncio
import ipaddress
from urllib.parse import urlsplit

from ragz.core.config import Settings
from ragz.core.errors import SsrfBlocked

# Generous but bounded -- this guards against pathological input, not a
# realistic URL/hostname (RFC 1035 caps a hostname at 253 chars; URLs in
# practice never approach this either).
_MAX_TARGET_LENGTH = 2048

# Mirrors `core/config.py::_production_fails_closed`'s env gate: only these
# two are "real", publicly reachable deployment modes.
_ENFORCED_ENVIRONMENTS = ("production", "staging")

# RFC 6598 shared address space (Carrier-Grade NAT) -- not covered by
# `ipaddress.IPv4Address.is_private` in the stdlib, so checked explicitly.
_CGNAT_NETWORK = ipaddress.ip_network("100.64.0.0/10")


def is_blocked_ip(ip: str) -> bool:
    """True if `ip` (IPv4 or IPv6, textual form) is private (RFC 1918),
    loopback, link-local (incl. the 169.254.169.254 cloud metadata address
    and IPv6 fe80::/10), CGNAT (100.64.0.0/10), unspecified (0.0.0.0 / ::),
    multicast, or otherwise IANA-reserved (incl. IPv6 ULA fc00::/7, which
    `ipaddress` already folds into `is_private`).

    An unparseable string is treated as blocked -- fail closed, never treat
    unknown input as "safe to dial"."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return True
    return (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_unspecified
        or addr.is_reserved
        or addr.is_multicast
        or (isinstance(addr, ipaddress.IPv4Address) and addr in _CGNAT_NETWORK)
    )


async def _assert_resolves_public(host: str) -> None:
    """Resolve `host` off-loop and raise `SsrfBlocked` if ANY returned
    address is blocked. A resolution failure itself is also `SsrfBlocked`
    (fail closed) rather than left to surface as a generic connection error
    later, and rather than silently treated as "no addresses, so safe"."""
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(host, None)
    except OSError as exc:
        raise SsrfBlocked(f"could not resolve host {host!r}") from exc
    if not infos:
        raise SsrfBlocked(f"host {host!r} resolved to no addresses")
    for *_rest, sockaddr in infos:
        ip = str(sockaddr[0])
        if is_blocked_ip(ip):
            raise SsrfBlocked(
                f"host {host!r} resolves to a blocked address ({ip}) -- "
                "private/loopback/link-local/metadata targets are not permitted"
            )


async def assert_public_url(url: str, settings: Settings, *, require_https: bool = True) -> None:
    """Guard an admin/OIDC-configurable URL (OIDC discovery/token/JWKS,
    provider base URLs) before it is dialed. No-op outside production/
    staging. In production/staging: requires an https scheme (unless
    `require_https=False`) and a host, then resolves the host and rejects
    it if any resolved IP `is_blocked_ip`."""
    if settings.environment not in _ENFORCED_ENVIRONMENTS:
        return
    if not url or len(url) > _MAX_TARGET_LENGTH:
        raise SsrfBlocked("URL is empty or exceeds the maximum allowed length")
    parsed = urlsplit(url)
    if require_https and parsed.scheme != "https":
        raise SsrfBlocked(f"URL scheme must be https, got {parsed.scheme!r}")
    if not parsed.hostname:
        raise SsrfBlocked("URL has no host")
    await _assert_resolves_public(parsed.hostname)


async def assert_public_host(host: str, settings: Settings) -> None:
    """Guard an admin-configurable bare host (SMTP host, no scheme) before
    it is dialed. No-op outside production/staging; otherwise resolves
    `host` and rejects it if any resolved IP `is_blocked_ip`."""
    if settings.environment not in _ENFORCED_ENVIRONMENTS:
        return
    if not host or len(host) > _MAX_TARGET_LENGTH:
        raise SsrfBlocked("host is empty or exceeds the maximum allowed length")
    await _assert_resolves_public(host)
