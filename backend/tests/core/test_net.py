"""sec RAGZ-PUB-11: central SSRF egress guard.

`is_blocked_ip` is pure and needs no fixtures. `assert_public_url`/
`assert_public_host` are environment-gated (no-op outside production/
staging) and resolve DNS via `asyncio.get_running_loop().getaddrinfo` --
tests monkeypatch that resolution so nothing here touches the network.
"""

from pathlib import Path

import pytest

from ragz.core import net
from ragz.core.config import Settings
from ragz.core.errors import SsrfBlocked
from ragz.modules.secrets.crypto import ensure_kek


@pytest.mark.parametrize(
    "ip",
    [
        "127.0.0.1",  # loopback
        "10.0.0.1",  # RFC 1918
        "172.16.0.1",  # RFC 1918
        "192.168.1.1",  # RFC 1918
        "169.254.169.254",  # link-local incl. cloud metadata
        "169.254.1.1",  # link-local
        "100.64.0.1",  # CGNAT (RFC 6598)
        "0.0.0.0",  # unspecified  # noqa: S104
        "::1",  # IPv6 loopback
        "fc00::1",  # IPv6 ULA
        "fe80::1",  # IPv6 link-local
        "::",  # IPv6 unspecified
        "not-an-ip",  # unparseable -- fail closed
    ],
)
def test_is_blocked_ip_blocks_private_loopback_link_local_cgnat_metadata(ip: str) -> None:
    assert net.is_blocked_ip(ip) is True


@pytest.mark.parametrize("ip", ["8.8.8.8", "1.1.1.1", "93.184.216.34", "2606:4700:4700::1111"])
def test_is_blocked_ip_allows_public_addresses(ip: str) -> None:
    assert net.is_blocked_ip(ip) is False


class _FakeLoop:
    """Stand-in for the running event loop: only `getaddrinfo` is used by
    `core/net.py`, so that's the only method faked."""

    def __init__(self, result: list[tuple[object, ...]] | Exception) -> None:
        self._result = result

    async def getaddrinfo(self, host: str, port: object) -> list[tuple[object, ...]]:
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


def _dns_answers(*ips: str) -> list[tuple[object, ...]]:
    # Real getaddrinfo() tuples: (family, type, proto, canonname, sockaddr).
    # sockaddr is (ip, port) for IPv4 and (ip, port, flowinfo, scopeid) for
    # IPv6 -- `core/net.py` only ever reads sockaddr[0], so a 2-tuple is
    # sufficient for both cases here.
    return [(None, None, None, "", (ip, 0)) for ip in ips]


def _patch_dns(
    monkeypatch: pytest.MonkeyPatch, result: list[tuple[object, ...]] | Exception
) -> None:
    monkeypatch.setattr(net.asyncio, "get_running_loop", lambda: _FakeLoop(result))


@pytest.fixture
def valid_kek_file(tmp_path: Path) -> str:
    path = tmp_path / "kek"
    ensure_kek(str(path))
    return str(path)


@pytest.fixture
def production_settings(valid_kek_file: str) -> Settings:
    # Mirrors tests/core/test_production_config.py's `safe_kwargs` -- every
    # field the fail-closed validator checks must be overridden so
    # constructing this fixture doesn't itself raise. Built as a dict (not
    # passed as literal kwargs) so ruff's hardcoded-password heuristic
    # (S106) doesn't fire on these deliberately-fake test values.
    kwargs: dict[str, object] = {
        "_env_file": None,
        "environment": "production",
        "api_key_pepper": "a-real-random-pepper-value",
        "database_url": "postgresql+asyncpg://ragz_prod:s3cret-prod-pw-2026@db.internal:5432/ragz",
        "minio_secret_key": "a-real-minio-secret",
        "litellm_master_key": "sk-a-real-litellm-master-key",
        "public_api_base_url": "https://api.example.com",
        "frontend_base_url": "https://app.example.com",
        "kek_file": valid_kek_file,
    }
    return Settings(**kwargs)  # type: ignore[arg-type]


@pytest.fixture
def dev_settings(valid_kek_file: str) -> Settings:
    return Settings(_env_file=None, environment="dev", kek_file=valid_kek_file)


@pytest.fixture
def test_environment_settings(valid_kek_file: str) -> Settings:
    return Settings(_env_file=None, environment="test", kek_file=valid_kek_file)


async def test_assert_public_url_blocks_url_resolving_to_private_ip_in_production(
    production_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_dns(monkeypatch, _dns_answers("10.0.0.5"))
    with pytest.raises(SsrfBlocked):
        await net.assert_public_url("https://internal.example.com/discovery", production_settings)


async def test_assert_public_url_blocks_metadata_ip_in_production(
    production_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_dns(monkeypatch, _dns_answers("169.254.169.254"))
    with pytest.raises(SsrfBlocked):
        await net.assert_public_url("https://metadata.example.com/", production_settings)


async def test_assert_public_url_allows_public_ip_in_production(
    production_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_dns(monkeypatch, _dns_answers("8.8.8.8"))
    await net.assert_public_url("https://idp.example.com/.well-known/openid-configuration",
                                 production_settings)  # must not raise


async def test_assert_public_url_rejects_http_when_https_required_in_production(
    production_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_dns(monkeypatch, _dns_answers("8.8.8.8"))
    with pytest.raises(SsrfBlocked):
        await net.assert_public_url("http://idp.example.com/", production_settings)


async def test_assert_public_url_allows_http_when_https_not_required_in_production(
    production_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_dns(monkeypatch, _dns_answers("8.8.8.8"))
    await net.assert_public_url(
        "http://idp.example.com/", production_settings, require_https=False
    )  # must not raise


async def test_assert_public_url_is_noop_in_dev_even_for_localhost(
    dev_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    # DNS resolution must not even be attempted -- if it were, this would
    # raise (the fake loop always raises), proving the no-op short-circuits
    # before ever reaching `_assert_resolves_public`.
    _patch_dns(monkeypatch, RuntimeError("DNS should not be resolved in dev"))
    await net.assert_public_url("http://localhost:8080/.well-known/openid-configuration",
                                 dev_settings)


async def test_assert_public_url_is_noop_in_test_environment(
    test_environment_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_dns(monkeypatch, RuntimeError("DNS should not be resolved in test"))
    await net.assert_public_url("http://localhost:8080/token", test_environment_settings)


async def test_assert_public_host_blocks_private_host_in_production(
    production_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_dns(monkeypatch, _dns_answers("192.168.1.50"))
    with pytest.raises(SsrfBlocked):
        await net.assert_public_host("internal-mail.corp", production_settings)


async def test_assert_public_host_allows_public_host_in_production(
    production_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_dns(monkeypatch, _dns_answers("93.184.216.34"))
    await net.assert_public_host("smtp.sendgrid.net", production_settings)  # must not raise


async def test_assert_public_host_is_noop_in_dev_even_for_localhost(
    dev_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_dns(monkeypatch, RuntimeError("DNS should not be resolved in dev"))
    await net.assert_public_host("localhost", dev_settings)


async def test_assert_public_url_blocks_dns_resolution_failure_in_production(
    production_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_dns(monkeypatch, OSError("name resolution failed"))
    with pytest.raises(SsrfBlocked):
        await net.assert_public_url("https://nonexistent.example.invalid/", production_settings)
