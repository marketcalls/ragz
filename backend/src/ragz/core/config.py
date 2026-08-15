from functools import lru_cache
from typing import Literal
from urllib.parse import urlsplit

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Known dev-only default credentials (RAGZ-PUB-05): a production deployment
# that still carries one of these literal values almost certainly means an
# operator forgot to override it, not that they deliberately chose it -- so
# the fail-closed validator below rejects each one by exact match.
_DEV_DEFAULT_DATABASE_URL = "postgresql+asyncpg://ragz:ragz@localhost:55432/ragz"
_DEV_DEFAULT_MINIO_SECRET_KEY = "ragz-dev-123"  # noqa: S105 (dev-only default literal, compared not used)
_DEV_DEFAULT_LITELLM_MASTER_KEY = "sk-ragz-dev-master"  # noqa: S105 (dev-only default literal, compared not used)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="RAGZ_", env_file=".env", extra="ignore")

    database_url: str = _DEV_DEFAULT_DATABASE_URL
    redis_url: str = "redis://localhost:56379/0"
    # "dev" = local/dev defaults allowed; "test" = pytest/httpx harness (also
    # relaxes the refresh-cookie Secure flag so the ASGI test client's
    # plain-http requests can carry it); "staging"/"production" are real
    # deployments -- "production" additionally triggers the fail-closed
    # validator below. An unrecognized value (e.g. a typo'd "prod") now fails
    # validation instead of silently behaving like a truthy non-dev string.
    environment: Literal["dev", "test", "staging", "production"] = "dev"
    kek_file: str = "./data/ragz_kek"
    access_token_ttl_seconds: int = 900
    refresh_token_ttl_seconds: int = 1209600  # 14 days
    # Server-side pepper (HMAC key) for opaque bearer-token hashes: refresh
    # tokens and invitation tokens. Empty = plain SHA-256 (backward compatible);
    # set it and those tokens are stored as HMAC-SHA256(pepper, token) so a
    # read-only DB leak can't verify or forge a token hash without this
    # out-of-DB secret. Iron rule 3 sanctions it as bootstrap-class config
    # (like the KEK source), NOT a stored secret. Rotating it invalidates all
    # live sessions and pending invitations (users re-login) — expected.
    api_key_pepper: str = ""
    # Concurrent tabs race on the same refresh cookie: the loser presents an
    # already-rotated token. Reuse within this window (with a live successor in
    # the family) reissues instead of tripping theft detection; reuse outside
    # it still revokes the whole family. 0 disables the grace window.
    refresh_reuse_grace_seconds: int = 10

    # Plan B: ingestion & retrieval
    qdrant_url: str = "http://localhost:56333"
    minio_endpoint: str = "http://localhost:59000"
    minio_access_key: str = "ragz"
    minio_secret_key: str = _DEV_DEFAULT_MINIO_SECRET_KEY  # noqa: S105 (dev-only default; prod overrides via env)
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
    litellm_master_key: str = _DEV_DEFAULT_LITELLM_MASTER_KEY  # noqa: S105
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

    @model_validator(mode="after")
    def _production_fails_closed(self) -> "Settings":
        """RAGZ-PUB-05: production must not silently run with dev defaults.
        dev/test/staging stay permissive; environment == "production" rejects
        each known-insecure condition below with a clear message."""
        if self.environment != "production":
            return self

        errors: list[str] = []
        if not self.api_key_pepper.strip():
            errors.append(
                "api_key_pepper is empty -- production must set RAGZ_API_KEY_PEPPER "
                "(the plain-SHA256 fallback is not acceptable outside dev/test)"
            )
        if self.database_url == _DEV_DEFAULT_DATABASE_URL:
            errors.append("database_url is still the dev default -- set RAGZ_DATABASE_URL")
        if self.minio_secret_key == _DEV_DEFAULT_MINIO_SECRET_KEY:
            errors.append("minio_secret_key is still the dev default -- set RAGZ_MINIO_SECRET_KEY")
        if self.litellm_master_key == _DEV_DEFAULT_LITELLM_MASTER_KEY:
            errors.append(
                "litellm_master_key is still the dev default -- set RAGZ_LITELLM_MASTER_KEY"
            )
        if self.public_api_base_url.startswith("http://"):
            errors.append("public_api_base_url uses http:// -- production requires https")
        elif not urlsplit(self.public_api_base_url).hostname:
            # RAGZ-PUB-09 review (Imp1): a host-less public_api_base_url (e.g.
            # "https://" or empty) would make trusted_hosts_for fall open to
            # ["*"], silently disabling the Host allowlist. Reject at load.
            errors.append(
                "public_api_base_url has no parseable host -- set a full "
                "https://<host> origin (the Host allowlist derives from it)"
            )
        if self.frontend_base_url.startswith("http://"):
            errors.append("frontend_base_url uses http:// -- production requires https")
        if not self.kek_file.strip():
            errors.append(
                "kek_file is empty -- production must point RAGZ_KEK_FILE at a real KEK source"
            )

        if errors:
            raise ValueError("insecure production configuration: " + "; ".join(errors))
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
