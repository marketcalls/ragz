from datetime import datetime

from pydantic import BaseModel, Field


class SecretWrite(BaseModel):
    """Write-only payload. There is deliberately no schema that returns a value."""

    value: str = Field(min_length=1, max_length=8192)


class SecretOut(BaseModel):
    name: str
    fingerprint: str
    last_used_at: datetime | None

    model_config = {"from_attributes": True}
