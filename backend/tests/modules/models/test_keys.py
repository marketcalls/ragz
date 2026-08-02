import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from ragz.core.config import Settings
from ragz.modules.auth.models import User
from ragz.modules.models.keys import get_or_create_user_virtual_key, update_user_budget
from ragz.modules.secrets import service as secrets_service
from ragz.modules.tenancy.models import Organization


async def _user(session: AsyncSession) -> User:
    org = Organization(name="VKOrg")
    session.add(org)
    await session.flush()
    user = User(org_id=org.id, email="vk@vk.com", password_hash="x", role="user")  # noqa: S106
    session.add(user)
    await session.commit()
    return user


def _gateway(calls: list[httpx.Request]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path == "/key/generate":
            return httpx.Response(200, json={"key": "sk-vkey-123"})
        if request.url.path == "/key/update":
            return httpx.Response(200, json={})
        return httpx.Response(404)

    return httpx.MockTransport(handler)


async def test_generate_once_then_reuse(
    session: AsyncSession, test_settings: Settings
) -> None:
    user = await _user(session)
    calls: list[httpx.Request] = []
    key = await get_or_create_user_virtual_key(
        session, test_settings, user_id=user.id, monthly_tokens=1_000_000,
        transport=_gateway(calls),
    )
    assert key == "sk-vkey-123"
    assert [c.url.path for c in calls] == ["/key/generate"]
    import json

    body = json.loads(calls[0].content)
    assert body["key_alias"] == f"ragz-user-{user.id}"
    assert body["budget_duration"] == "30d"
    assert body["max_budget"] == 5.0  # 1M tokens at the default $5/1M mirror rate

    # stored encrypted, fetched without a second /key/generate
    names = [s.name for s in await secrets_service.list_secrets(session)]
    assert f"vkey:{user.id}" in names
    again = await get_or_create_user_virtual_key(
        session, test_settings, user_id=user.id, monthly_tokens=1_000_000,
        transport=_gateway(calls),
    )
    assert again == "sk-vkey-123"
    assert len(calls) == 1


async def test_gateway_down_returns_none(
    session: AsyncSession, test_settings: Settings
) -> None:
    user = await _user(session)

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    key = await get_or_create_user_virtual_key(
        session, test_settings, user_id=user.id, monthly_tokens=None,
        transport=httpx.MockTransport(boom),
    )
    assert key is None  # caller falls back to master key


async def test_update_budget_skips_without_key(
    session: AsyncSession, test_settings: Settings
) -> None:
    user = await _user(session)
    calls: list[httpx.Request] = []
    await update_user_budget(session, test_settings, user_id=user.id,
                             monthly_tokens=500_000, transport=_gateway(calls))
    assert calls == []  # no stored key -> nothing to update
