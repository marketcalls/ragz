from uuid import UUID

from sqlalchemy.orm import Mapped, mapped_column

from raghub.core.db import Base, UUIDPk

# Fixed id for the bootstrap-seeded, non-deletable local TEI embedding model
# (Task 1 migration inserts the row with this exact id; Workspace.embedding_model_id
# defaults to it at workspace creation, Task 5). A literal constant, not a
# uuid5 derivation, since nothing else needs to re-derive it.
LOCAL_EMBEDDING_MODEL_ID = UUID("00000000-0000-4000-8000-000000000001")


class Model(UUIDPk, Base):
    __tablename__ = "models"

    litellm_model_name: Mapped[str] = mapped_column(unique=True)
    display_name: Mapped[str]
    provider_kind: Mapped[str]  # openai | ollama | openai_compatible | litellm | tei
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
    # Reasoning-effort control (2026-07-20 design): admin-set capability flag
    # + default tier. Manual toggle, not auto-detected from LiteLLM's price
    # snapshot (that data isn't in this table) — see the design doc for why.
    supports_reasoning: Mapped[bool] = mapped_column(default=False, server_default="false")
    default_reasoning_effort: Mapped[str] = mapped_column(default="off", server_default="off")
    # DOC-9 (per-chat ephemeral attachments): admin-set capability flag so the
    # chat/agent loop knows this model can accept image content blocks. Plain
    # boolean, no tiered setting (unlike supports_reasoning) - vision is on/off.
    supports_vision: Mapped[bool] = mapped_column(default=False, server_default="false")
    # DOC-10 (2026-07-24 design): "chat" (default, every pre-existing row) or
    # "embedding". Embedding-only fields below stay NULL for chat rows and
    # vice versa -- one table, unused columns per row, same convention as
    # mock_response already coexisting unused for provider_kinds that don't
    # need it.
    modality: Mapped[str] = mapped_column(default="chat", server_default="chat")
    # Admin-entered at creation, not auto-detected (matches supports_reasoning/
    # supports_vision's manual-toggle convention) -- required only when
    # modality="embedding"; enforced at the schema/service layer, not a DB
    # constraint (this table mixes both modalities in one column set).
    dimension: Mapped[int | None] = mapped_column(default=None)
    # The Qdrant collection this embedding model's vectors live in. Computed
    # once at creation (service.create_model) and never changed after --
    # switching it would silently orphan a live collection. NULL for chat
    # rows. The seeded local model (LOCAL_EMBEDDING_MODEL_ID) gets the
    # LITERAL pre-existing "chunks_bge_m3" value (Task 1 migration) so this
    # plan never moves or re-embeds any already-indexed data; every
    # subsequently created embedding model gets f"chunks_{id.hex}".
    collection_name: Mapped[str | None] = mapped_column(default=None)
