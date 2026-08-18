"""Read-only views of tenancy entities, for other modules to consume.

Phase 2 item 2 of the 2026-08-17 architecture review: modules were importing
each other's ORM models, which CLAUDE.md's Never Do list forbids ("No ORM
objects across module boundaries"). chat, for instance, took a real Workspace
just to read eight scalar settings off it -- so it depended on tenancy's schema,
its lazy-loading behaviour and its session lifetime to answer questions like
"is web search on?".

A frozen view carries exactly the fields the consumer needs and nothing it can
mutate or lazily load. Being detached from the session, it also cannot trigger a
query mid-stream, which is the failure the streaming work in this same phase was
about.
"""

from dataclasses import dataclass
from uuid import UUID

from ragz.modules.tenancy.models import Workspace


@dataclass(frozen=True, slots=True)
class WorkspaceView:
    """The workspace settings that shape a chat turn.

    Exactly the attributes consumers read today -- deliberately not a mirror of
    the table. A field belongs here when another module needs it, not because
    the column exists.
    """

    id: UUID
    embedding_model_id: UUID
    top_k: int
    system_prompt_override: str | None
    fallback_policy: str
    web_search_enabled: bool
    generative_ui_enabled: bool
    strict_mode: bool

    @classmethod
    def of(cls, workspace: Workspace) -> "WorkspaceView":
        """Build from the ORM row. The one place that touches both, so tenancy
        keeps ownership of its own schema."""
        return cls(
            id=workspace.id,
            embedding_model_id=workspace.embedding_model_id,
            top_k=workspace.top_k,
            system_prompt_override=workspace.system_prompt_override,
            fallback_policy=workspace.fallback_policy,
            web_search_enabled=workspace.web_search_enabled,
            generative_ui_enabled=workspace.generative_ui_enabled,
            strict_mode=workspace.strict_mode,
        )
