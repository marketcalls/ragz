"""sec RAGZ-PUB-06 follow-up: deterministic, spoof-resistant real-client-IP
resolution. Pure logic (no DB/Redis/network) -- a minimal fake Request
stand-in is enough; `Settings(_env_file=None, ...)` builds config directly
without touching the environment (same pattern as tests/core/test_config.py).
"""

from dataclasses import dataclass, field

import pytest

from ragz.core.client_ip import client_ip
from ragz.core.config import Settings


@dataclass
class _FakeClient:
    host: str


@dataclass
class _FakeRequest:
    client: _FakeClient | None
    headers: dict[str, str] = field(default_factory=dict)


def _req(peer: str | None, xff: str | None = None) -> _FakeRequest:
    headers = {"x-forwarded-for": xff} if xff is not None else {}
    return _FakeRequest(client=_FakeClient(peer) if peer is not None else None, headers=headers)


def _settings(trusted_proxies: list[str]) -> Settings:
    return Settings(_env_file=None, trusted_proxies=trusted_proxies)


def test_empty_trusted_proxies_returns_peer_and_ignores_xff() -> None:
    settings = _settings([])
    request = _req("203.0.113.9", xff="1.2.3.4")
    assert client_ip(request, settings) == "203.0.113.9"


def test_trusted_proxy_peer_returns_rightmost_non_trusted_xff_hop() -> None:
    settings = _settings(["10.0.0.1/32"])
    request = _req("10.0.0.1", xff="1.2.3.4, 10.0.0.1")
    assert client_ip(request, settings) == "1.2.3.4"


def test_trusted_proxy_peer_multi_hop_skips_multiple_trusted_hops() -> None:
    """Two trusted proxies in the chain (e.g. a CDN in front of an internal
    LB) -- walk from the right past BOTH before landing on the real client."""
    settings = _settings(["10.0.0.1/32", "10.0.0.2/32"])
    request = _req("10.0.0.2", xff="1.2.3.4, 10.0.0.1, 10.0.0.2")
    assert client_ip(request, settings) == "1.2.3.4"


def test_trusted_proxy_peer_xff_only_trusted_hops_falls_back_to_peer() -> None:
    """Documented choice: if every hop in XFF is itself a trusted proxy (no
    real client address survived), fall back to the peer -- never guess at
    the unverified leftmost entry."""
    settings = _settings(["10.0.0.1/32", "10.0.0.2/32"])
    request = _req("10.0.0.2", xff="10.0.0.1, 10.0.0.2")
    assert client_ip(request, settings) == "10.0.0.2"


def test_untrusted_peer_spoofed_xff_is_ignored() -> None:
    """The key spoof-resistance guarantee: a direct (non-proxy) caller can
    set X-Forwarded-For to anything -- it must never be honored."""
    settings = _settings(["10.0.0.1/32"])
    request = _req("9.9.9.9", xff="9.9.9.9")  # attacker-supplied, peer != trusted proxy
    assert client_ip(request, settings) == "9.9.9.9"


def test_trusted_proxy_peer_missing_xff_falls_back_to_peer() -> None:
    settings = _settings(["10.0.0.1/32"])
    request = _req("10.0.0.1", xff=None)
    assert client_ip(request, settings) == "10.0.0.1"


def test_trusted_proxy_peer_empty_xff_falls_back_to_peer() -> None:
    settings = _settings(["10.0.0.1/32"])
    request = _req("10.0.0.1", xff="   ")
    assert client_ip(request, settings) == "10.0.0.1"


def test_malformed_xff_entries_are_skipped_without_crashing() -> None:
    settings = _settings(["10.0.0.1/32"])
    request = _req("10.0.0.1", xff="not-an-ip, , 1.2.3.4, garbage!!")
    # rightmost non-trusted, well-formed hop -- "garbage!!" is skipped
    assert client_ip(request, settings) == "1.2.3.4"


def test_malformed_xff_only_garbage_falls_back_to_peer() -> None:
    settings = _settings(["10.0.0.1/32"])
    request = _req("10.0.0.1", xff="not-an-ip, also-not-an-ip")
    assert client_ip(request, settings) == "10.0.0.1"


def test_ipv6_peer_and_xff_resolve_correctly() -> None:
    settings = _settings(["fd00::1/128"])
    request = _req("fd00::1", xff="2001:db8::abcd, fd00::1")
    assert client_ip(request, settings) == "2001:db8::abcd"


def test_ipv6_untrusted_peer_ignores_spoofed_xff() -> None:
    settings = _settings(["fd00::1/128"])
    request = _req("2001:db8::9", xff="2001:db8::9")
    assert client_ip(request, settings) == "2001:db8::9"


def test_cidr_range_trusted_proxy_matches_any_address_in_range() -> None:
    settings = _settings(["10.0.0.0/8"])
    request = _req("10.4.5.6", xff="1.2.3.4, 10.4.5.6")
    assert client_ip(request, settings) == "1.2.3.4"


def test_malformed_trusted_proxies_config_entry_never_trusts_and_never_crashes() -> None:
    settings = _settings(["not-a-cidr!!"])
    request = _req("9.9.9.9", xff="1.2.3.4")
    assert client_ip(request, settings) == "9.9.9.9"


def test_no_request_client_falls_back_to_unknown_without_crashing() -> None:
    settings = _settings([])
    request = _req(None, xff="1.2.3.4")
    assert client_ip(request, settings) == "unknown"


@pytest.mark.parametrize(
    "trusted_proxies_input",
    [
        ["10.0.0.1", "10.0.0.2"],  # bare IPs, no CIDR suffix
        ["10.0.0.1/32", "10.0.0.2/32"],
    ],
)
def test_bare_ip_and_explicit_cidr_are_equivalent(trusted_proxies_input: list[str]) -> None:
    settings = _settings(trusted_proxies_input)
    request = _req("10.0.0.1", xff="7.7.7.7, 10.0.0.1")
    assert client_ip(request, settings) == "7.7.7.7"


def test_trusted_proxies_accepts_comma_separated_env_string(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RAGZ_TRUSTED_PROXIES", "10.0.0.0/8, 172.16.5.4")
    settings = Settings(_env_file=None)
    assert settings.trusted_proxies == ["10.0.0.0/8", "172.16.5.4"]
