from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="RAGZ_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://ragz:ragz@localhost:55432/ragz"
    redis_url: str = "redis://localhost:56379/0"
    environment: str = "dev"
    kek_file: str = "./data/ragz_kek"
    access_token_ttl_seconds: int = 900
    refresh_token_ttl_seconds: int = 1209600  # 14 days
    # Concurrent tabs race on the same refresh cookie: the loser presents an
    # already-rotated token. Reuse within this window (with a live successor in
    # the family) reissues instead of tripping theft detection; reuse outside
    # it still revokes the whole family. 0 disables the grace window.
    refresh_reuse_grace_seconds: int = 10

    # Plan B: ingestion & retrieval
    qdrant_url: str = "http://localhost:56333"
    minio_endpoint: str = "http://localhost:59000"
    minio_access_key: str = "ragz"
    minio_secret_key: str = "ragz123"  # noqa: S105 (dev-only default; prod overrides via env)
    minio_bucket: str = "ragz-documents"
    tei_url: str = "http://localhost:58080"
    embedding_backend: str = "tei"  # "tei" | "hash" (hash = deterministic, test/dev only)
    embedding_dim: int = 1024  # bge-m3
    # Plan E: cross-encoder reranker (CHAT-2 pull-forward)
    rerank_url: str = "http://localhost:58081"
    rerank_backend: str = "tei"  # "tei" | "lexical" (lexical = deterministic, test/dev only)
    max_upload_mb: int = 100
    interactive_upload_mb: int = 10  # uploads below this jump to the interactive queue

    # Plan C: LiteLLM proxy gateway
    litellm_url: str = "http://localhost:54000"
    # Dev-only default; override in any real deployment. This is the proxy's own
    # admin credential (bootstrap-class config), NOT a provider key (iron rule 3).
    litellm_master_key: str = "sk-ragz-dev-master"  # noqa: S105
    chat_context_token_budget: int = 8000

    # Phase 2 Plan F: OIDC SSO (AUTH-2)
    public_api_base_url: str = "http://localhost:8000"  # builds the OIDC redirect_uri
    frontend_base_url: str = "http://localhost:5173"    # post-callback redirect target

    # QUOTA-3 backstop: USD mirrored onto per-user LiteLLM virtual keys per 1M
    # tokens of allocation. 0 disables max_budget (local-only installs).
    litellm_usd_per_million_tokens: float = 5.0

    # Plan G Task 3: connection-pool sizing (was hardcoded; now tunable per deployment).
    db_pool_size: int = 10
    db_max_overflow: int = 20
    db_pool_timeout_seconds: int = 30
    redis_max_connections: int = 100
    httpx_max_connections: int = 100
    httpx_max_keepalive: int = 20

    # Plan G Task 5: optional Sentry error reporting. Empty string = off (zero
    # dependency cost unless sentry-sdk is also installed via the observability group).
    sentry_dsn: str = ""

    # Plan G Task 12 (MODEL-10/G7): LiteLLM's pricing/context-window catalog sync.
    # Empty string = air-gap mode: bundled snapshot only, no network call ever.
    model_catalog_url: str = (
        "https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json"
    )

    # Plan H: OCR (DOC-3)
    ocr_enabled: bool = True  # kill-switch; detection itself is automatic
    ocr_min_chars_per_page: int = 200

    # Phase 3 Plan I (D7): Tavily web search. The API KEY is a stored secret
    # ("tavily", iron rule 3) — only the endpoint URL lives in config.
    tavily_url: str = "https://api.tavily.com"


@lru_cache
def get_settings() -> Settings:
    return Settings()
