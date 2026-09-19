from datetime import datetime
from uuid import UUID

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index, text
from sqlalchemy.orm import Mapped, mapped_column

from ragz.core.db import Base, UUIDPk


class OrgQuota(Base):
    __tablename__ = "org_quotas"

    org_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"), primary_key=True)
    monthly_tokens: Mapped[int]
    default_user_monthly_tokens: Mapped[int | None] = mapped_column(default=None)
    reset_day: Mapped[int] = mapped_column(default=1)  # 1..31, clamped per month


class UserQuota(Base):
    __tablename__ = "user_quotas"

    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), primary_key=True)
    monthly_tokens: Mapped[int]


class UsageRecord(UUIDPk, Base):
    __tablename__ = "usage_records"
    __table_args__ = (
        Index("ix_usage_org_created", "org_id", "created_at"),
        Index("ix_usage_user_created", "user_id", "created_at"),
        Index("ix_usage_workspace_created", "workspace_id", "created_at"),
        Index(
            "uq_usage_records_idempotency_key",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
        ),
    )

    org_id: Mapped[UUID]
    user_id: Mapped[UUID]
    # Pure reporting dimension (department = workspace): tags each ledger row
    # with the workspace it belongs to. Nullable -- platform-level ops have no
    # workspace. NEVER part of any token/units aggregation.
    workspace_id: Mapped[UUID | None] = mapped_column(default=None)
    model_id: Mapped[UUID | None] = mapped_column(default=None)
    feature: Mapped[str]  # chat | ingestion | embedding | rerank | web_search
    prompt_tokens: Mapped[int]
    completion_tokens: Mapped[int]
    # Per-call features (rerank search-units, web_search calls) count here.
    # Token features leave it 0. NEVER summed into any token aggregation:
    # units are calls, not tokens, and must not inflate a token budget.
    units: Mapped[int] = mapped_column(default=0, server_default="0")
    idempotency_key: Mapped[str | None] = mapped_column(default=None)


class ResourceReservation(UUIDPk, Base):
    """Durable in-flight admission counted before external storage work."""

    __tablename__ = "resource_reservations"
    __table_args__ = (
        CheckConstraint("size_bytes >= 0", name="ck_resource_reservations_size"),
        CheckConstraint(
            "kind IN ('document', 'attachment')", name="ck_resource_reservations_kind"
        ),
        Index("ix_resource_reservations_org_kind_expiry", "org_id", "kind", "expires_at"),
        Index("ix_resource_reservations_user_kind_expiry", "user_id", "kind", "expires_at"),
    )

    org_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE")
    )
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    kind: Mapped[str]
    size_bytes: Mapped[int] = mapped_column(BigInteger())
    expires_at: Mapped[datetime]
