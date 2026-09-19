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
    assert s.max_upload_mb == 1_024
    assert s.interactive_upload_mb == 50
    assert s.document_max_text_mb == 32
    assert s.liteparse_batch_pages == 250
    assert s.liteparse_max_pages == 10_000
    assert s.query_embedding_cache_enabled is False
    assert s.query_embedding_cache_max_entries == 10_000
    assert s.query_embedding_cache_ttl_seconds == 3_600
    assert s.query_expansion_cache_enabled is False
    assert s.query_expansion_cache_max_entries == 5_000
    assert s.query_expansion_cache_ttl_seconds == 3_600
    assert s.multi_query_expansion_timeout_ms == 3_000
    assert s.cohere_rerank_max_retries == 2
    assert s.cohere_rerank_base_backoff_seconds == 0.5
    assert s.org_max_documents == 10_000
    assert s.org_max_storage_bytes == 100 * 1024 * 1024 * 1024


def test_liteparse_limits_read_env(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("RAGZ_LITEPARSE_BATCH_PAGES", "400")
    monkeypatch.setenv("RAGZ_LITEPARSE_MAX_PAGES", "12000")
    s = Settings(_env_file=None)
    assert s.liteparse_batch_pages == 400
    assert s.liteparse_max_pages == 12_000
