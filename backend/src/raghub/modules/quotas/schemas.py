"""Quota allocation schemas (QUOTA-1)."""

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
