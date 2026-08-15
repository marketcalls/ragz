from functools import lru_cache
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from ragz.core.crypto import load_kek
from ragz.core.errors import SecretsError

# Known dev-only default credentials (RAGZ-PUB-05): a production deployment
# that still carries one of these literal values almost certainly means an
# operator forgot to override it, not that they deliberately chose it -- so
# the fail-closed validator below rejects each one by exact match.
_DEV_DEFAULT_DATABASE_URL = "postgresql+asyncpg://ragz:ragz@localhost:55432/ragz"
_DEV_DEFAULT_MINIO_SECRET_KEY = "ragz-dev-123"  # noqa: S105 (dev-only default literal, compared not used)
_DEV_DEFAULT_LITELLM_MASTER_KEY = "sk-ragz-dev-master"  # noqa: S105 (dev-only default literal, compared not used)
# RAGZ-PUB-05 follow-up: the dev default database_url pairs the username
# "ragz" with the password "ragz" -- an operator who repoints the HOST (e.g.
# to a managed Postgres instance) but forgets to also change the credentials
# is just as exposed as one who left the whole URL untouched. The validator
# below checks the parsed userinfo components, not the literal string, so it
# catches "ragz:ragz@" against ANY host/port/db name.
_DEV_DEFAULT_DB_USER = "ragz"
_DEV_DEFAULT_DB_PASSWORD = "ragz"  # noqa: S105 (dev-only default literal, compared not used)
# RAGZ-PUB-05 follow-up: floor for machine-generated secrets (pepper, MinIO
# secret key, LiteLLM master key). 16 chars is a defensible minimum -- a
# randomly generated 16-char secret from a reasonable alphabet already clears
# ~90+ bits of entropy, well above what's brute-forceable, while still being
# short enough not to reject real (if unusually terse) production secrets.
# It is a length FLOOR, not an entropy/complexity check -- deliberately
# simple and fully deterministic.
_MIN_SECRET_LENGTH = 16


def _check_https_origin(value: str, field_name: str, errors: list[str]) -> None:
    """RAGZ-PUB-05 follow-up: production/staging origins must be an exact
    ``https://<host>`` origin -- not merely "not http". Any other scheme
    (``ftp://``, ``javascript:``, no scheme at all) or a missing hostname is
    rejected. Malformed values that `urlsplit` itself can't parse are also
    rejected rather than allowed to slip through."""
    try:
        parsed = urlsplit(value)
    except ValueError:
        errors.append(f"{field_name} is not a parseable URL (got {value!r})")
        return
    if parsed.scheme != "https" or not parsed.hostname:
        errors.append(
            f"{field_name} must be an https:// origin with a host (got {value!r}) -- "
            "production/staging reject any other scheme (http, ftp, javascript:, ...) "
            "or a missing/empty host"
        )


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

    # Cost reporting (design 2026-08-15 §2): estimated $ for per-call features.
    # Rerank bills in Cohere "search units"; web search bills per Tavily call.
    # 0.0 (default) hides $ for these features -- token features price off the
    # model_catalog cost/token instead. Reporting-only; never on the hot path.
    rerank_usd_per_call: float = 0.0
    web_search_usd_per_call: float = 0.0

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

    # sec RAGZ-PUB-13: external API keys must never be perpetual. Any key
    # created with no explicit expires_at is bounded to now + this many days;
    # any caller-supplied expires_at further out than that is capped to the
    # same ceiling (modules/auth/api_keys_service.generate_api_key). Also used
    # at auth time as the grace window for legacy pre-fix rows that still
    # carry a NULL expires_at (resolve_api_key treats those as expiring at
    # created_at + this many days, not never).
    api_key_max_lifetime_days: int = 90

    # sec RAGZ-PUB-08 (residual): run_agent_gather's web_search budget
    # (DEFAULT_WEB_SEARCH_BUDGET) resets every planner turn, so consent + a
    # per-turn cap alone still let a user run unlimited external searches
    # across a conversation/day. These add a PERSISTENT, Redis-backed
    # fixed-window cap enforced in agent.execute_tool BEFORE the searcher is
    # ever called (modules/chat/agent.py). 0 disables the given cap
    # (unlimited). Per-org is an additional, opt-in belt-and-braces layer --
    # off by default so a fresh install only pays for the per-user cap.
    web_search_daily_limit_per_user: int = 50
    web_search_daily_limit_per_org: int = 0

    # sec RAGZ-PUB-03 (bounded slice): per-ORGANIZATION resource caps enforced
    # at upload time (modules/documents/service.py::create_from_upload), so a
    # single tenant cannot fill shared Postgres/Qdrant/object storage without
    # bound. 0 = disabled (the default) -- a fresh install / dev / test keeps
    # today's unbounded behavior with zero configuration. Other PUB-03
    # dimensions (OCR pages, embedding tokens, queue depth, job concurrency,
    # provider spend) are intentionally OUT of this slice's scope.
    org_max_documents: int = 0
    org_max_storage_bytes: int = 0

    # sec RAGZ-PUB-06 follow-up: IPs/CIDRs of this deployment's own trusted
    # reverse proxy(ies)/CDN egress ranges (e.g. an ALB, nginx, Cloudflare).
    # Empty (the default) means trust NONE -- every request's real client IP
    # is the direct TCP peer, exactly as before this setting existed, so a
    # fresh install / dev / test needs zero configuration to keep working.
    # Set this in production ONLY when the app sits behind a real reverse
    # proxy: `core/client_ip.py::client_ip` then honors `X-Forwarded-For`,
    # but ONLY when the immediate peer is one of these networks -- from any
    # other peer, XFF is attacker-controlled and is ignored outright. Accepts
    # either a comma-separated string (e.g. "10.0.0.0/8, 172.16.5.4") via env
    # var or a real list programmatically; bare IPs are treated as /32 (or
    # /128 for IPv6).
    trusted_proxies: Annotated[list[str], NoDecode] = []

    @field_validator("trusted_proxies", mode="before")
    @classmethod
    def _split_trusted_proxies(cls, value: object) -> object:
        """`NoDecode` above stops pydantic-settings' default JSON-array
        decode for this field (which would reject a plain comma-separated
        env var with a confusing JSONDecodeError) -- this validator does the
        actual parsing instead. A `str` (from the env var) is split on
        commas; anything else (e.g. a list passed in directly, as tests do)
        passes through unchanged."""
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @model_validator(mode="after")
    def _production_fails_closed(self) -> "Settings":
        """RAGZ-PUB-05: production/staging must not silently run with dev
        defaults or otherwise-insecure config. dev/test stay permissive;
        environment in ("production", "staging") -- both are public
        deployment modes -- rejects each known-insecure condition below with
        a clear message. staging was originally exempted from this guard;
        that was itself a finding (a public staging deployment is just as
        exposed as production) so it now shares the exact same checks."""
        if self.environment not in ("production", "staging"):
            return self

        errors: list[str] = []

        # -- api_key_pepper: non-empty AND long enough --
        if not self.api_key_pepper.strip():
            errors.append(
                "api_key_pepper is empty -- production/staging must set RAGZ_API_KEY_PEPPER "
                "(the plain-SHA256 fallback is not acceptable outside dev/test)"
            )
        elif len(self.api_key_pepper) < _MIN_SECRET_LENGTH:
            errors.append(
                f"api_key_pepper is only {len(self.api_key_pepper)} chars -- "
                f"production/staging require at least {_MIN_SECRET_LENGTH}"
            )

        # -- database_url: reject the default "ragz:ragz" CREDENTIALS, not just
        # the exact default URL string -- a re-hosted default is just as weak. --
        try:
            db_parsed = urlsplit(self.database_url)
        except ValueError:
            errors.append(f"database_url is not a parseable URL (got {self.database_url!r})")
        else:
            _default_creds = (
                db_parsed.username == _DEV_DEFAULT_DB_USER
                and db_parsed.password == _DEV_DEFAULT_DB_PASSWORD
            )
            if _default_creds:
                errors.append(
                    "database_url uses the default 'ragz:ragz' credentials (checked by "
                    "username/password, independent of host/port/db name) -- set "
                    "RAGZ_DATABASE_URL with unique production credentials"
                )
            # RAGZ-PUB-05 follow-up: the default-credentials check above only
            # catches the literal "ragz:ragz" pair -- a re-hosted URL with a
            # unique username but a trivially short (or entirely absent)
            # password, e.g. postgresql+asyncpg://user:x@host/db, previously
            # sailed through unnoticed. Enforced independent of the
            # default-credentials check (both can fire together). Reuses
            # _MIN_SECRET_LENGTH (16) -- the same floor as every other
            # machine-generated secret in this validator; a length check
            # only, not complexity, for the same reasons documented above.
            if not db_parsed.username:
                errors.append(
                    "database_url has no username -- production/staging must set a "
                    "real database username"
                )
            _db_password_len = len(db_parsed.password) if db_parsed.password else 0
            if _db_password_len < _MIN_SECRET_LENGTH:
                errors.append(
                    f"database_url password is missing or only {_db_password_len} chars -- "
                    f"production/staging require at least {_MIN_SECRET_LENGTH}"
                )
        # Belt-and-suspenders: still reject the exact literal default too (covers
        # any future default whose credentials aren't a clean userinfo pair).
        if self.database_url == _DEV_DEFAULT_DATABASE_URL:
            errors.append("database_url is still the dev default -- set RAGZ_DATABASE_URL")

        # -- minio_secret_key / litellm_master_key: reject the known dev-default
        # token AND enforce the same minimum length as any other secret. --
        if self.minio_secret_key == _DEV_DEFAULT_MINIO_SECRET_KEY:
            errors.append("minio_secret_key is still the dev default -- set RAGZ_MINIO_SECRET_KEY")
        if len(self.minio_secret_key) < _MIN_SECRET_LENGTH:
            errors.append(
                f"minio_secret_key is only {len(self.minio_secret_key)} chars -- "
                f"production/staging require at least {_MIN_SECRET_LENGTH}"
            )
        if self.litellm_master_key == _DEV_DEFAULT_LITELLM_MASTER_KEY:
            errors.append(
                "litellm_master_key is still the dev default -- set RAGZ_LITELLM_MASTER_KEY"
            )
        if len(self.litellm_master_key) < _MIN_SECRET_LENGTH:
            errors.append(
                f"litellm_master_key is only {len(self.litellm_master_key)} chars -- "
                f"production/staging require at least {_MIN_SECRET_LENGTH}"
            )

        # -- public_api_base_url / frontend_base_url: exact https:// origin with a
        # host -- not merely "not http://". Catches ftp://, javascript:, etc. --
        _check_https_origin(self.public_api_base_url, "public_api_base_url", errors)
        _check_https_origin(self.frontend_base_url, "frontend_base_url", errors)

        # -- kek_file: non-empty path that actually resolves to a valid 32-byte
        # KEK. load_kek() is the single sanctioned KEK-loading path (core/crypto.py);
        # reusing it here means "does the validator accept this kek_file" and "will
        # the app actually be able to decrypt secrets with it" can never disagree. --
        if not self.kek_file.strip():
            errors.append(
                "kek_file is empty -- production/staging must point RAGZ_KEK_FILE at a "
                "real KEK source"
            )
        else:
            try:
                load_kek(self.kek_file)
            except (SecretsError, OSError, ValueError) as exc:
                # SecretsError: missing file or wrong-length key (load_kek's own
                # checks). OSError: exists-but-unreadable (permission denied,
                # etc.) -- load_kek() only typed-guards the missing-file case.
                # ValueError: malformed content that isn't even valid base64
                # (base64.binascii.Error is a ValueError subclass).
                errors.append(f"kek_file is not a valid, readable KEK source: {exc}")

        if errors:
            raise ValueError("insecure production configuration: " + "; ".join(errors))
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
