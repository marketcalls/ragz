from sqlalchemy.orm import Mapped, mapped_column

from raghub.core.db import Base, UUIDPk


class Model(UUIDPk, Base):
    __tablename__ = "models"

    litellm_model_name: Mapped[str] = mapped_column(unique=True)
    display_name: Mapped[str]
    provider_kind: Mapped[str]  # openai | ollama | openai_compatible
    base_url: Mapped[str | None] = mapped_column(default=None)
    enabled: Mapped[bool] = mapped_column(default=True)
    sync_status: Mapped[str] = mapped_column(default="pending")  # pending | synced | error
