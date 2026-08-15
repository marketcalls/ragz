from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from ragz.core.db import Base, UUIDPk
from ragz.modules.models.models import LOCAL_EMBEDDING_MODEL_ID


class Organization(UUIDPk, Base):
    __tablename__ = "organizations"

    name: Mapped[str] = mapped_column(unique=True)
    # AUTH-6: lowercase email domains whose users may JIT-provision via SSO.
    sso_domains: Mapped[list[str] | None] = mapped_column(ARRAY(String), default=None)


class Workspace(UUIDPk, Base):
    __tablename__ = "workspaces"
    __table_args__ = (
        CheckConstraint(
            "chunk_method IN ('heading', 'fixed', 'page', 'table_qa')",
            name="ck_workspaces_chunk_method",
        ),
    )

    org_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"), index=True)
    name: Mapped[str]
    embedding_model_id: Mapped[UUID] = mapped_column(
        ForeignKey("models.id"), default=LOCAL_EMBEDDING_MODEL_ID
    )
    min_score: Mapped[float] = mapped_column(default=0.35)
    default_model_id: Mapped[UUID | None] = mapped_column(default=None)
    # Plan E (ADM-3): per-workspace retrieval tuning
    top_k: Mapped[int] = mapped_column(default=8)
    rerank_enabled: Mapped[bool] = mapped_column(default=False)
    system_prompt_override: Mapped[str | None] = mapped_column(Text(), default=None)
    # Phase 3 Plan I (design D3): RAG-miss policy — "general_knowledge" | "decline"
    fallback_policy: Mapped[str] = mapped_column(
        default="general_knowledge", server_default="general_knowledge"
    )
    # Phase 3 Plan I Task 11 (D7): gates the agent loop's web_search tool. Default
    # OFF -- also requires fallback_policy != "decline" and a stored tavily secret
    # (chat/service.py's use_web gate), so this column alone never exposes the web.
    web_search_enabled: Mapped[bool] = mapped_column(default=False, server_default="false")
    # Phase 3 Plan J (design D4/§3): synchronous Gatekeeper validation with
    # one critique-guided regeneration, before the final answer streams.
    strict_mode: Mapped[bool] = mapped_column(default=False, server_default="false")
    # Plan K §4: ingestion enrichment toggle (utility-model per-chunk metadata)
    enrichment_enabled: Mapped[bool] = mapped_column(default=False, server_default="false")
    # Chunk-methods plan Task 2: workspace-default chunking strategy for
    # ingests; a document may opt out via Document.chunk_method_override.
    chunk_method: Mapped[str] = mapped_column(default="heading", server_default="heading")
    # In-chat generative UI (design 2026-08-15, §2): gates the extra
    # "visualize" model call in chat/service.py::stream_reply that emits
    # Message.blocks_json + the `blocks` SSE frame. Default OFF, same
    # ADD COLUMN NOT NULL + server_default posture as web_search_enabled --
    # off means today's plain-markdown behavior, byte-identical.
    generative_ui_enabled: Mapped[bool] = mapped_column(default=False, server_default="false")


class WorkspaceMember(Base):
    __tablename__ = "workspace_members"
    # RBAC-08: role has never gated any authorization decision -- this CHECK
    # constraint just closes the "arbitrary free string" gap so the value is
    # trustworthy metadata going forward. The paired forward migration
    # backfills any pre-existing non-conforming value to 'contributor'.
    __table_args__ = (
        CheckConstraint(
            "role IN ('owner', 'manager', 'contributor', 'viewer')",
            name="ck_workspace_members_role",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id"), primary_key=True
    )
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), primary_key=True)
    role: Mapped[str] = mapped_column(default="contributor")


class Group(UUIDPk, Base):
    __tablename__ = "groups"
    __table_args__ = (UniqueConstraint("org_id", "name", name="uq_groups_org_name"),)

    org_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"), index=True)
    name: Mapped[str]


class UserGroup(Base):
    __tablename__ = "user_groups"

    group_id: Mapped[UUID] = mapped_column(
        ForeignKey("groups.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )


class RoleTemplate(UUIDPk, Base):
    """Plan H (RBAC-2): superadmin-built, GLOBAL (no org_id per the owner's
    requirement) named bundle of permission flags that org admins assign to
    role="user" accounts.

    RBAC-09: templates carry a draft|active|archived lifecycle and a
    monotonic version. New app-created templates default to "draft" here
    (ORM-level default) -- deliberately asymmetric with the migration's
    server_default="active", which only backfills EXISTING rows so they stay
    immediately assignable. See the versioning migration for the full
    rationale."""

    __tablename__ = "role_templates"
    __table_args__ = (
        CheckConstraint(
            "status IN ('draft', 'active', 'archived')", name="ck_role_templates_status"
        ),
    )

    name: Mapped[str] = mapped_column(unique=True)
    description: Mapped[str] = mapped_column(default="")
    permissions: Mapped[list[str]] = mapped_column(ARRAY(String))
    status: Mapped[str] = mapped_column(default="draft")
    version: Mapped[int] = mapped_column(default=1)


class RoleTemplateVersion(UUIDPk, Base):
    """Immutable snapshot written on every activate_role_template call (RBAC-09).
    Never updated or deleted by application code -- rollback_role_template
    reads the PREVIOUS row and writes a NEW one, same append-only posture as
    AuditEvent."""

    __tablename__ = "role_template_versions"
    __table_args__ = (
        UniqueConstraint("role_template_id", "version", name="uq_role_template_versions"),
    )

    role_template_id: Mapped[UUID] = mapped_column(
        ForeignKey("role_templates.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[int]
    permissions: Mapped[list[str]] = mapped_column(ARRAY(String))
