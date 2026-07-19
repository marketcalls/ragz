from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

# "litellm": any LiteLLM-native provider (anthropic, gemini, groq, ...) —
# litellm_model_name is passed to the gateway VERBATIM (catalog names for
# non-openai providers already carry their prefix, e.g. gemini/gemini-2.5-pro);
# no base_url needed, api_key attached as usual.
ProviderKind = Literal["openai", "ollama", "openai_compatible", "litellm"]


class ModelCreate(BaseModel):
    litellm_model_name: str = Field(min_length=1, max_length=200)
    display_name: str = Field(min_length=1, max_length=200)
    provider_kind: ProviderKind
    base_url: str | None = None
    api_key: str | None = None  # write-only: stored via the secrets module, never returned
    # Superadmin-only fake-LLM passthrough (D2) - never exposed on ModelPublic.
    mock_response: str | None = None
    # Phase 3 Plan I (MODEL-3): superadmin can flag a model as unreliable at
    # native tool calling from creation time.
    tools_unreliable: bool = False

    @model_validator(mode="after")
    def _base_url_required_for_self_hosted(self) -> "ModelCreate":
        if self.provider_kind in ("ollama", "openai_compatible") and not self.base_url:
            raise ValueError("base_url is required for ollama and openai_compatible providers")
        return self


class ModelPatch(BaseModel):
    display_name: str | None = None
    base_url: str | None = None
    enabled: bool | None = None
    api_key: str | None = None  # write-only
    mock_response: str | None = None
    # Phase 3 Plan I (MODEL-3): superadmin toggle for the JSON-planner fallback.
    tools_unreliable: bool | None = None


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


class ModelPublic(BaseModel):
    """What non-superadmin users see (chat model picker)."""

    id: UUID
    display_name: str

    model_config = {"from_attributes": True}
