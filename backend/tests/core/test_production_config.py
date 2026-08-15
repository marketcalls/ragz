"""RAGZ-PUB-05 (release blocker) + follow-up hardening: production/staging
must fail closed instead of silently running with dev defaults or otherwise
insecure config. `environment` is a closed enum, and `environment in
("production", "staging")` triggers a model_validator that rejects: an empty
or too-short pepper; the default 'ragz:ragz' DB credentials (checked by
parsed username/password, independent of host/port/db name); dev-default
MinIO/LiteLLM tokens or too-short secrets; any public_api_base_url /
frontend_base_url that isn't an exact https:// origin with a host (ftp://,
javascript:, http://, hostless); and an empty/missing/unreadable/wrong-length
KEK file. dev/test stay permissive (no regression for local dev or CI).
staging was originally exempted from this validator -- that was itself part
of the finding, since a public staging deployment is exposed the same way
production is -- so it now shares the exact same checks (see the `staging`
variants below)."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from ragz.core.config import Settings
from ragz.modules.secrets.crypto import ensure_kek


@pytest.fixture
def valid_kek_file(tmp_path: Path) -> str:
    path = tmp_path / "kek"
    ensure_kek(str(path))
    return str(path)


@pytest.fixture
def safe_kwargs(valid_kek_file: str) -> dict[str, object]:
    # A fully "safe" production configuration -- every field the validator
    # checks is overridden away from its dev default / insecure value.
    return {
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


@pytest.mark.parametrize("env", ["production", "staging"])
def test_empty_pepper_raises(safe_kwargs: dict[str, object], env: str) -> None:
    kwargs = dict(safe_kwargs, environment=env, api_key_pepper="")
    with pytest.raises(ValueError, match="api_key_pepper"):
        Settings(**kwargs)  # type: ignore[arg-type]


def test_short_pepper_raises(safe_kwargs: dict[str, object]) -> None:
    kwargs = dict(safe_kwargs, api_key_pepper="short1")
    with pytest.raises(ValueError, match="api_key_pepper"):
        Settings(**kwargs)  # type: ignore[arg-type]


def test_dev_default_minio_secret_raises(safe_kwargs: dict[str, object]) -> None:
    kwargs = dict(safe_kwargs, minio_secret_key="ragz-dev-123")  # noqa: S106
    with pytest.raises(ValueError, match="minio_secret_key"):
        Settings(**kwargs)  # type: ignore[arg-type]


def test_short_minio_secret_raises(safe_kwargs: dict[str, object]) -> None:
    kwargs = dict(safe_kwargs, minio_secret_key="short1")  # noqa: S106
    with pytest.raises(ValueError, match="minio_secret_key"):
        Settings(**kwargs)  # type: ignore[arg-type]


def test_dev_default_litellm_master_key_raises(safe_kwargs: dict[str, object]) -> None:
    kwargs = dict(safe_kwargs, litellm_master_key="sk-ragz-dev-master")  # noqa: S106
    with pytest.raises(ValueError, match="litellm_master_key"):
        Settings(**kwargs)  # type: ignore[arg-type]


def test_short_litellm_master_key_raises(safe_kwargs: dict[str, object]) -> None:
    kwargs = dict(safe_kwargs, litellm_master_key="sk-short")  # noqa: S106
    with pytest.raises(ValueError, match="litellm_master_key"):
        Settings(**kwargs)  # type: ignore[arg-type]


def test_dev_default_database_url_raises(safe_kwargs: dict[str, object]) -> None:
    kwargs = dict(
        safe_kwargs,
        database_url="postgresql+asyncpg://ragz:ragz@localhost:55432/ragz",
    )
    with pytest.raises(ValueError, match="database_url"):
        Settings(**kwargs)  # type: ignore[arg-type]


def test_default_db_credentials_on_other_host_raises(safe_kwargs: dict[str, object]) -> None:
    # RAGZ-PUB-05 follow-up: the exact-literal check alone missed this -- same
    # default 'ragz:ragz' credentials, but re-hosted to a "real looking" host.
    kwargs = dict(
        safe_kwargs,
        database_url="postgresql+asyncpg://ragz:ragz@postgres:5432/ragz",
    )
    with pytest.raises(ValueError, match="database_url"):
        Settings(**kwargs)  # type: ignore[arg-type]


def test_default_db_credentials_different_port_and_db_raises(
    safe_kwargs: dict[str, object],
) -> None:
    kwargs = dict(
        safe_kwargs,
        database_url="postgresql+asyncpg://ragz:ragz@db.prod.internal:6543/otherdb",
    )
    with pytest.raises(ValueError, match="database_url"):
        Settings(**kwargs)  # type: ignore[arg-type]


def test_short_database_password_raises(safe_kwargs: dict[str, object]) -> None:
    # RAGZ-PUB-05 follow-up: a unique (non-default) username paired with a
    # trivially short password previously slipped past the default-
    # credentials check entirely.
    kwargs = dict(
        safe_kwargs,
        database_url="postgresql+asyncpg://ragz_prod:x@db.internal:5432/ragz",
    )
    with pytest.raises(ValueError, match="database_url password"):
        Settings(**kwargs)  # type: ignore[arg-type]


def test_missing_database_password_raises(safe_kwargs: dict[str, object]) -> None:
    kwargs = dict(
        safe_kwargs,
        database_url="postgresql+asyncpg://ragz_prod@db.internal:5432/ragz",
    )
    with pytest.raises(ValueError, match="database_url password"):
        Settings(**kwargs)  # type: ignore[arg-type]


def test_missing_database_username_raises(safe_kwargs: dict[str, object]) -> None:
    kwargs = dict(
        safe_kwargs,
        database_url="postgresql+asyncpg://:a-strong-16-char-pw@db.internal:5432/ragz",
    )
    with pytest.raises(ValueError, match="database_url has no username"):
        Settings(**kwargs)  # type: ignore[arg-type]


def test_strong_database_password_with_non_default_creds_constructs(
    safe_kwargs: dict[str, object],
) -> None:
    kwargs = dict(
        safe_kwargs,
        database_url="postgresql+asyncpg://ragz_prod:a-strong-16-char-pw@db.internal:5432/ragz",
    )
    s = Settings(**kwargs)  # type: ignore[arg-type]
    assert s.database_url.endswith("db.internal:5432/ragz")


def test_dev_default_db_password_still_permitted_in_dev() -> None:
    # No regression: local dev's "ragz:ragz@localhost" URL is unaffected --
    # the whole validator is a no-op outside production/staging.
    s = Settings(_env_file=None)
    assert s.environment == "dev"


def test_http_public_api_base_url_raises(safe_kwargs: dict[str, object]) -> None:
    kwargs = dict(safe_kwargs, public_api_base_url="http://api.example.com")
    with pytest.raises(ValueError, match="public_api_base_url"):
        Settings(**kwargs)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "value",
    ["ftp://api.example.com", "javascript:alert(1)", "https://", "not-a-url", ""],
)
def test_public_api_base_url_non_https_or_hostless_raises(
    safe_kwargs: dict[str, object], value: str
) -> None:
    kwargs = dict(safe_kwargs, public_api_base_url=value)
    with pytest.raises(ValueError, match="public_api_base_url"):
        Settings(**kwargs)  # type: ignore[arg-type]


def test_http_frontend_base_url_raises(safe_kwargs: dict[str, object]) -> None:
    kwargs = dict(safe_kwargs, frontend_base_url="http://app.example.com")
    with pytest.raises(ValueError, match="frontend_base_url"):
        Settings(**kwargs)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "value",
    ["ftp://app.example.com", "javascript:alert(1)", "https://", "not-a-url", ""],
)
def test_frontend_base_url_non_https_or_hostless_raises(
    safe_kwargs: dict[str, object], value: str
) -> None:
    kwargs = dict(safe_kwargs, frontend_base_url=value)
    with pytest.raises(ValueError, match="frontend_base_url"):
        Settings(**kwargs)  # type: ignore[arg-type]


def test_empty_kek_file_raises(safe_kwargs: dict[str, object]) -> None:
    kwargs = dict(safe_kwargs, kek_file="")
    with pytest.raises(ValueError, match="kek_file"):
        Settings(**kwargs)  # type: ignore[arg-type]


def test_missing_kek_file_raises(safe_kwargs: dict[str, object], tmp_path: Path) -> None:
    kwargs = dict(safe_kwargs, kek_file=str(tmp_path / "does-not-exist"))
    with pytest.raises(ValueError, match="kek_file"):
        Settings(**kwargs)  # type: ignore[arg-type]


def test_corrupt_kek_file_raises(safe_kwargs: dict[str, object], tmp_path: Path) -> None:
    # Right path, wrong content: not a valid base64-encoded 32-byte key.
    path = tmp_path / "bad_kek"
    path.write_bytes(b"too-short")
    kwargs = dict(safe_kwargs, kek_file=str(path))
    with pytest.raises(ValueError, match="kek_file"):
        Settings(**kwargs)  # type: ignore[arg-type]


@pytest.mark.parametrize("env", ["production", "staging"])
def test_all_safe_values_constructs(safe_kwargs: dict[str, object], env: str) -> None:
    s = Settings(**dict(safe_kwargs, environment=env))  # type: ignore[arg-type]
    assert s.environment == env


def test_staging_shares_the_same_checks_as_production(safe_kwargs: dict[str, object]) -> None:
    # RAGZ-PUB-05 follow-up: staging used to be exempt from this validator
    # entirely -- it must now reject the same insecure config production does.
    kwargs = dict(safe_kwargs, environment="staging", api_key_pepper="")
    with pytest.raises(ValueError, match="api_key_pepper"):
        Settings(**kwargs)  # type: ignore[arg-type]


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
