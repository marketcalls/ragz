import json
import time
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from raghub.modules.tenancy.context import (
    TenantContext,
    get_tenant_context,
    rate_limit_user,
    require_role,
)

router = APIRouter(tags=["ops"])
CtxDep = Annotated[TenantContext, Depends(get_tenant_context)]
ReportCtxDep = Annotated[TenantContext, Depends(rate_limit_user("client_errors", 10, 60))]
SuperadminDep = Annotated[TenantContext, Depends(require_role())]

_KEY = "client_errors"
_MAX = 200


class ClientErrorIn(BaseModel):
    message: str = Field(max_length=2000)
    stack: str | None = Field(default=None, max_length=8000)
    url: str | None = Field(default=None, max_length=500)


class ClientErrorOut(ClientErrorIn):
    ts: float
    org_id: str
    user_id: str


@router.post("/client-errors", status_code=204)
async def report_client_error(
    body: ClientErrorIn, request: Request, ctx: ReportCtxDep
) -> None:
    entry = ClientErrorOut(
        **body.model_dump(), ts=time.time(),
        org_id=str(ctx.org_id), user_id=str(ctx.user_id),
    )
    redis = request.app.state.redis
    pipe = redis.pipeline()
    pipe.lpush(_KEY, entry.model_dump_json())
    pipe.ltrim(_KEY, 0, _MAX - 1)
    await pipe.execute()


@router.get("/superadmin/client-errors", response_model=list[ClientErrorOut])
async def list_client_errors(
    request: Request, ctx: SuperadminDep, limit: int = 50
) -> list[ClientErrorOut]:
    raw = await request.app.state.redis.lrange(_KEY, 0, min(limit, _MAX) - 1)
    return [ClientErrorOut(**json.loads(r)) for r in raw]
