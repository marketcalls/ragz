from sqlalchemy.orm import Mapped, mapped_column

from raghub.core.db import Base, UUIDPk


class Model(UUIDPk, Base):
    __tablename__ = "models"

    litellm_model_name: Mapped[str] = mapped_column(unique=True)
    display_name: Mapped[str]
    provider_kind: Mapped[str]  # openai | ollama | openai_compatible | litellm
    base_url: Mapped[str | None] = mapped_column(default=None)
    enabled: Mapped[bool] = mapped_column(default=True)
    sync_status: Mapped[str] = mapped_column(default="pending")  # pending | synced | error
    # Superadmin-only fake-LLM passthrough (D2): LiteLLM's native mock_response
    # litellm_param streams this canned text without hitting any real provider
    # - useful for load tests, demos, and air-gapped dev.
    mock_response: Mapped[str | None] = mapped_column(default=None)
