from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="RAGHUB_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://raghub:raghub@localhost:55432/raghub"
    redis_url: str = "redis://localhost:56379/0"
    environment: str = "dev"
    kek_file: str = "./data/raghub_kek"
    access_token_ttl_seconds: int = 900
    refresh_token_ttl_seconds: int = 1209600  # 14 days

    # Plan B: ingestion & retrieval
    qdrant_url: str = "http://localhost:56333"
    minio_endpoint: str = "http://localhost:59000"
    minio_access_key: str = "raghub"
    minio_secret_key: str = "raghub123"  # noqa: S105 (dev-only default; prod overrides via env)
    minio_bucket: str = "raghub-documents"
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
    litellm_master_key: str = "sk-raghub-dev-master"  # noqa: S105
    chat_context_token_budget: int = 8000

    # Phase 2 Plan F: OIDC SSO (AUTH-2)
    public_api_base_url: str = "http://localhost:8000"  # builds the OIDC redirect_uri
    frontend_base_url: str = "http://localhost:5173"    # post-callback redirect target


@lru_cache
def get_settings() -> Settings:
    return Settings()
