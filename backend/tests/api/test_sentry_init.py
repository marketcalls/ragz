"""Sentry init is optional (Plan G Task 5): only fires when RAGZ_SENTRY_DSN is
set, and a missing sentry-sdk dependency must degrade to a warning, never a
startup crash. sentry-sdk is an optional ``observability`` extra, so these
tests fake the module in sys.modules rather than depending on it being
installed."""

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from ragz.core.config import get_settings


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> None:
    # create_app() reads settings via the module-level lru_cache'd get_settings(),
    # so env changes need a cache clear to take effect for the next create_app().
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_sentry_init_called_with_dsn_and_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RAGZ_SENTRY_DSN", "https://public@fake.example/1")
    monkeypatch.setenv("RAGZ_ENVIRONMENT", "staging")
    fake_sentry_sdk = SimpleNamespace(init=MagicMock())
    monkeypatch.setitem(sys.modules, "sentry_sdk", fake_sentry_sdk)

    from ragz.api.app import create_app

    create_app()

    fake_sentry_sdk.init.assert_called_once()
    _, kwargs = fake_sentry_sdk.init.call_args
    assert kwargs["dsn"] == "https://public@fake.example/1"
    assert kwargs["environment"] == "staging"


def test_sentry_init_missing_package_does_not_crash(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RAGZ_SENTRY_DSN", "https://public@fake.example/1")
    # A module mapped to None in sys.modules forces `import sentry_sdk` to raise
    # ImportError, simulating the optional dependency not being installed.
    monkeypatch.setitem(sys.modules, "sentry_sdk", None)

    from ragz.api.app import create_app

    create_app()  # must not raise
