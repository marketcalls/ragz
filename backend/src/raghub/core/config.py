from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="RAGHUB_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://raghub:raghub@localhost:55432/raghub"
    redis_url: str = "redis://localhost:56379/0"
    environment: str = "dev"
    access_token_ttl_seconds: int = 900
    refresh_token_ttl_seconds: int = 1209600  # 14 days


@lru_cache
def get_settings() -> Settings:
    return Settings()
