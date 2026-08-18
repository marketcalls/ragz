from ragz.core.config import Settings


def test_settings_reads_env(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("RAGZ_DATABASE_URL", "postgresql+asyncpg://x:y@h:5432/db")
    s = Settings(_env_file=None)
    assert s.database_url.endswith("/db")
    assert s.access_token_ttl_seconds == 900


def test_ingestion_settings_defaults(pristine_env, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    s = Settings(_env_file=None)
    assert s.qdrant_url == "http://localhost:56333"
    assert s.minio_endpoint == "http://localhost:59000"
    assert s.minio_bucket == "ragz-documents"
    assert s.tei_url == "http://localhost:58080"
    assert s.embedding_backend == "tei"
    assert s.embedding_dim == 1024
    assert s.interactive_upload_mb == 50
