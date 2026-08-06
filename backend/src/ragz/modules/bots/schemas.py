"""Write-only credential schemas (iron rule 3): BotIntegrationCreate.token/
.signing_secret are accepted on create and NEVER echoed back -- unlike API
keys, the superadmin supplies these themselves, so there is no "shown once"
response field at all; BotIntegrationOut has no credential field, period."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel

Platform = Literal["telegram", "discord", "slack"]


class BotIntegrationCreate(BaseModel):
    platform: Platform
    name: str
    workspace_id: UUID
    user_id: UUID
    token: str
    signing_secret: str


class BotIntegrationPatch(BaseModel):
    enabled: bool


class BotIntegrationOut(BaseModel):
    id: UUID
    platform: Platform
    name: str
    org_id: UUID
    workspace_id: UUID
    user_id: UUID
    webhook_id: UUID
    webhook_url: str
    enabled: bool
    created_by: UUID
    created_at: datetime
