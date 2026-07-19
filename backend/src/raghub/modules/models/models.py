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
    # Phase 3 Plan I (MODEL-3): model can't do native tool calling reliably;
    # the agent loop uses the JSON-planner protocol for it instead.
    tools_unreliable: Mapped[bool] = mapped_column(default=False, server_default="false")
    # Phase 3 Plan J (D5/§4): superadmin-designated utility model for
    # validation/eval-judging/enrichment/memory. Exactly one row may be True
    # at a time — enforced in service.update_model, not by a DB constraint
    # (a partial unique index on a boolean is possible but the "clear
    # others in the same transaction" rule is simpler to reason about and
    # test at the service layer, matching update_document_current's
    # promotion-flip precedent rather than adding new SQL).
    is_utility: Mapped[bool] = mapped_column(default=False, server_default="false")
