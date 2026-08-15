from uuid import UUID

from sqlalchemy import ForeignKey, Index
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
    )

    org_id: Mapped[UUID]
    user_id: Mapped[UUID]
    model_id: Mapped[UUID | None] = mapped_column(default=None)
    feature: Mapped[str]  # chat | ingestion | embedding | rerank | web_search
    prompt_tokens: Mapped[int]
    completion_tokens: Mapped[int]
    # Per-call features (rerank search-units, web_search calls) count here.
    # Token features leave it 0. NEVER summed into any token aggregation:
    # units are calls, not tokens, and must not inflate a token budget.
    units: Mapped[int] = mapped_column(default=0, server_default="0")
