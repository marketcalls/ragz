from raghub.core.config import Settings


def test_settings_reads_env(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("RAGHUB_DATABASE_URL", "postgresql+asyncpg://x:y@h:5432/db")
    s = Settings(_env_file=None)
    assert s.database_url.endswith("/db")
    assert s.access_token_ttl_seconds == 900
