"""RAGZ-PUB-05 (release blocker): production must fail closed instead of
silently running with dev defaults. `environment` is now a closed enum, and
`environment == "production"` triggers a model_validator that rejects an
empty pepper, any known dev-default credential, http:// public URLs, and an
empty KEK source. dev/test/staging stay permissive (no regression for local
dev or CI)."""

import pytest
from pydantic import ValidationError

from ragz.core.config import Settings

# A fully "safe" production configuration -- every field the validator checks
# is overridden away from its dev default.
_SAFE_PRODUCTION_KWARGS: dict[str, object] = {
    "_env_file": None,
    "environment": "production",
    "api_key_pepper": "a-real-random-pepper-value",
    "database_url": "postgresql+asyncpg://ragz_prod:s3cret-pw@db.internal:5432/ragz",
    "minio_secret_key": "a-real-minio-secret",
    "litellm_master_key": "sk-a-real-litellm-master-key",
    "public_api_base_url": "https://api.example.com",
    "frontend_base_url": "https://app.example.com",
    "kek_file": "/etc/ragz/kek",
}


def test_production_empty_pepper_raises() -> None:
    kwargs = dict(_SAFE_PRODUCTION_KWARGS, api_key_pepper="")
    with pytest.raises(ValueError, match="api_key_pepper"):
        Settings(**kwargs)  # type: ignore[arg-type]


def test_production_dev_default_minio_secret_raises() -> None:
    kwargs = dict(_SAFE_PRODUCTION_KWARGS, minio_secret_key="ragz-dev-123")  # noqa: S106
    with pytest.raises(ValueError, match="minio_secret_key"):
        Settings(**kwargs)  # type: ignore[arg-type]


def test_production_dev_default_litellm_master_key_raises() -> None:
    kwargs = dict(_SAFE_PRODUCTION_KWARGS, litellm_master_key="sk-ragz-dev-master")  # noqa: S106
    with pytest.raises(ValueError, match="litellm_master_key"):
        Settings(**kwargs)  # type: ignore[arg-type]


def test_production_dev_default_database_url_raises() -> None:
    kwargs = dict(
        _SAFE_PRODUCTION_KWARGS,
        database_url="postgresql+asyncpg://ragz:ragz@localhost:55432/ragz",
    )
    with pytest.raises(ValueError, match="database_url"):
        Settings(**kwargs)  # type: ignore[arg-type]


def test_production_http_public_api_base_url_raises() -> None:
    kwargs = dict(_SAFE_PRODUCTION_KWARGS, public_api_base_url="http://api.example.com")
    with pytest.raises(ValueError, match="public_api_base_url"):
        Settings(**kwargs)  # type: ignore[arg-type]


def test_production_http_frontend_base_url_raises() -> None:
    kwargs = dict(_SAFE_PRODUCTION_KWARGS, frontend_base_url="http://app.example.com")
    with pytest.raises(ValueError, match="frontend_base_url"):
        Settings(**kwargs)  # type: ignore[arg-type]


def test_production_empty_kek_file_raises() -> None:
    kwargs = dict(_SAFE_PRODUCTION_KWARGS, kek_file="")
    with pytest.raises(ValueError, match="kek_file"):
        Settings(**kwargs)  # type: ignore[arg-type]


def test_production_all_safe_values_constructs() -> None:
    s = Settings(**_SAFE_PRODUCTION_KWARGS)  # type: ignore[arg-type]
    assert s.environment == "production"


def test_dev_defaults_still_construct_without_error() -> None:
    # No regression: local dev / CI never sets these overrides.
    s = Settings(_env_file=None)
    assert s.environment == "dev"
    assert s.api_key_pepper == ""
    assert s.public_api_base_url.startswith("http://")


def test_test_environment_with_defaults_constructs() -> None:
    # The pytest/httpx harness uses environment="test" (see conftest.py's
    # test_settings fixture) and must stay permissive too.
    s = Settings(_env_file=None, environment="test")
    assert s.environment == "test"


def test_invalid_environment_value_raises() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, environment="prod")  # type: ignore[arg-type]
