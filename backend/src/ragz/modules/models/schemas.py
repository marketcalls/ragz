from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

# "litellm": any LiteLLM-native provider (anthropic, gemini, groq, ...) —
# litellm_model_name is passed to the gateway VERBATIM (catalog names for
# non-openai providers already carry their prefix, e.g. gemini/gemini-2.5-pro);
# no base_url needed, api_key attached as usual. "tei" is reserved for
# the single bootstrap-seeded local embedding model (DOC-10) -- never
# creatable via this API, see ModelCreate's validator below.
ProviderKind = Literal["openai", "ollama", "openai_compatible", "litellm", "tei"]

ReasoningEffort = Literal["off", "low", "medium", "high"]

ModelModality = Literal["chat", "embedding"]

# Generative UI Task 8: gates the generative-UI image pipeline (superadmin
# global, default "off"). "web_results" surfaces images already present in
# the turn's web-search results (Tavily); off -> no image fetch, no minting.
GenerativeUiImages = Literal["off", "web_results"]


class ModelCreate(BaseModel):
    litellm_model_name: str = Field(min_length=1, max_length=200)
    display_name: str = Field(min_length=1, max_length=200)
    provider_kind: ProviderKind
    base_url: str | None = Field(default=None, max_length=2000)
    api_key: str | None = Field(default=None, max_length=8192)
    # Superadmin-only fake-LLM passthrough (D2) - never exposed on ModelPublic.
    mock_response: str | None = Field(default=None, max_length=32000)
    # Phase 3 Plan I (MODEL-3): superadmin can flag a model as unreliable at
    # native tool calling from creation time.
    tools_unreliable: bool = False
    supports_reasoning: bool = False
    default_reasoning_effort: ReasoningEffort = "off"
    supports_vision: bool = False
    # DOC-10: "chat" (default, every pre-DOC-10 caller is unaffected) or
    # "embedding". dimension is required (and only meaningful) for embedding.
    modality: ModelModality = "chat"
    dimension: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _base_url_required_for_self_hosted(self) -> "ModelCreate":
        if self.provider_kind in ("ollama", "openai_compatible") and not self.base_url:
            raise ValueError("base_url is required for ollama and openai_compatible providers")
        return self

    @model_validator(mode="after")
    def _embedding_modality_shape(self) -> "ModelCreate":
        if self.provider_kind == "tei":
            raise ValueError(
                "provider_kind 'tei' is reserved for the built-in local embedding "
                "model and cannot be created via this API"
            )
        if self.modality == "embedding" and self.dimension is None:
            raise ValueError("dimension is required when modality is 'embedding'")
        if self.modality == "chat" and self.dimension is not None:
            raise ValueError("dimension only applies to modality 'embedding'")
        return self


class ModelPatch(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=200)
    base_url: str | None = Field(default=None, max_length=2000)
    enabled: bool | None = None
    api_key: str | None = Field(default=None, max_length=8192)  # write-only
    mock_response: str | None = Field(default=None, max_length=32000)
    # Phase 3 Plan I (MODEL-3): superadmin toggle for the JSON-planner fallback.
    tools_unreliable: bool | None = None
    supports_reasoning: bool | None = None
    default_reasoning_effort: ReasoningEffort | None = None
    supports_vision: bool | None = None
    # Phase 3 Plan J (D5/§4): setting True clears every OTHER model's flag in
    # the same transaction (service.update_model) — exactly one utility model.
    is_utility: bool | None = None
    # modality/dimension/collection_name are deliberately absent here --
    # immutable after creation (changing dimension would silently mismatch
    # the already-created Qdrant collection's vector size).


SyncStatus = Literal["synced", "error", "pending"]


class ModelOut(BaseModel):
    """Admin-page shape (Plan D renders every field, incl. fingerprint + sync state)."""

    id: UUID
    litellm_model_name: str
    display_name: str
    provider_kind: ProviderKind
    base_url: str | None
    enabled: bool
    key_fingerprint: str | None  # secrets fingerprint for model:{id}; None = keyless
    sync_status: SyncStatus
    mock_response: str | None  # superadmin-only fake-LLM passthrough (D2)
    # Phase 3 Plan I (MODEL-3): agent loop (Task 9) uses this to skip native
    # tool-calling and fall back to the JSON-planner protocol.
    tools_unreliable: bool
    supports_reasoning: bool
    default_reasoning_effort: ReasoningEffort
    supports_vision: bool
    # Phase 3 Plan J (D5/§4): superadmin-designated utility model. Exactly
    # one row is True at a time — enforced in service.update_model.
    is_utility: bool
    modality: ModelModality
    dimension: int | None
    collection_name: str | None


class ModelPublic(BaseModel):
    """What non-superadmin users see (chat model picker) -- unchanged shape;
    the route now filters to modality="chat" before serializing (Step 6)."""

    id: UUID
    display_name: str
    supports_reasoning: bool
    default_reasoning_effort: ReasoningEffort
    supports_vision: bool

    model_config = {"from_attributes": True}


class ProviderSettingsOut(BaseModel):
    document_parser: str
    rerank_provider: str
    cohere_rerank_model: str
    # DDG-01 selector: which web-search provider the chat route uses
    # ("duckduckgo" default/keyless, "tavily" cloud). tavily_key_set mirrors
    # the *_key_set booleans -- the Tavily key itself is write-only.
    web_search_provider: str
    # When True (default), DuckDuckGo results are enriched with full page
    # content (SSRF-guarded fetch) instead of short snippets.
    web_search_full_content: bool
    tavily_key_set: bool
    # Global default chunking strategy NEW workspaces inherit at creation.
    # The per-workspace override (Workspace.chunk_method) is unchanged.
    default_chunk_method: str
    # Global default embedding model NEW workspaces inherit at creation.
    # None -> the built-in local TEI model, which is what every workspace was
    # hardcoded to before this setting existed: Workspace.embedding_model_id
    # defaulted to LOCAL_EMBEDDING_MODEL_ID no matter which models were
    # actually enabled, so enabling a hosted embedder did not change what a
    # new workspace used and ingestion kept dialling a TEI that may not run.
    default_embedding_model_id: UUID | None
    llamaparse_key_set: bool
    cohere_key_set: bool
    generative_ui_images: GenerativeUiImages
    # Global superadmin gate for in-chat generative UI (the visualize step).
    # Default ON -- answers render as visual cards/tables/charts by default.
    generative_ui_enabled: bool


class ProviderSettingsUpdate(BaseModel):
    document_parser: Literal["anydoc", "docling", "llamaparse", "liteparse"] | None = None
    rerank_provider: Literal["local", "cohere"] | None = None
    cohere_rerank_model: Literal["rerank-v4.0-fast", "rerank-v4.0-pro"] | None = None
    web_search_provider: Literal["duckduckgo", "tavily"] | None = None
    web_search_full_content: bool | None = None
    default_chunk_method: Literal["heading", "fixed", "page", "table_qa"] | None = None
    generative_ui_images: GenerativeUiImages | None = None
    generative_ui_enabled: bool | None = None
    # Validated in settings_service against an existing modality=="embedding"
    # model -- a stale or chat-model id here would silently break ingestion for
    # every workspace created afterwards.
    default_embedding_model_id: UUID | None = None
    # write-only: accepted on input, NEVER echoed back (ProviderSettingsOut has
    # no key fields, only *_key_set booleans).
    llamaparse_api_key: str | None = Field(default=None, max_length=8192)
    cohere_api_key: str | None = Field(default=None, max_length=8192)
    tavily_api_key: str | None = Field(default=None, max_length=8192)
