"""Quota allocation schemas (QUOTA-1)."""

from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class OrgQuotaIn(BaseModel):
    monthly_tokens: int = Field(ge=0)
    default_user_monthly_tokens: int | None = Field(default=None, ge=0)
    reset_day: int = Field(default=1, ge=1, le=31)


class OrgQuotaOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    org_id: UUID
    monthly_tokens: int
    default_user_monthly_tokens: int | None
    reset_day: int


class UserQuotaIn(BaseModel):
    monthly_tokens: int | None = Field(default=None, ge=0)


class UserQuotaOut(BaseModel):
    user_id: UUID
    monthly_tokens: int | None
    used_tokens: int
    allocated_tokens: int | None
    resets_at: datetime


class UsageMeterOut(BaseModel):
    used_tokens: int
    allocated_tokens: int | None
    resets_at: datetime
    warning: bool


class DailyUsagePointOut(BaseModel):
    date: str  # YYYY-MM-DD
    prompt_tokens: int
    completion_tokens: int


class DayUsage(BaseModel):
    day: date
    tokens: int


class ModelUsage(BaseModel):
    model_id: UUID | None
    tokens: int


class UserUsage(BaseModel):
    user_id: UUID
    email: str
    tokens: int
    queries: int


class UsageKpis(BaseModel):
    queries: int
    total_tokens: int
    active_users: int
    no_answer_count: int


class DayCount(BaseModel):
    day: date
    count: int


class ModelDayTokens(BaseModel):
    day: date
    model_name: str
    tokens: int


class UsageSummaryOut(BaseModel):
    by_day: list[DayUsage]
    by_model: list[ModelUsage]
    by_user: list[UserUsage]
    kpis: UsageKpis
    queries_per_day: list[DayCount]
    tokens_by_model_per_day: list[ModelDayTokens]


class OrgUsage(BaseModel):
    org_id: UUID
    name: str
    tokens: int
